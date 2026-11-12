"""One-time codes sent by email: the second step of sign-in, and the confirmation for turning two-step sign-in on or off.

A code is 6 random digits, valid for `ttl` seconds and usable once. Only a keyed hash of it is kept, in the shared Redis
store (the same store as the sign-in throttle, so every API worker sees the same state):

- code:     opsdesk:otp:{purpose}:{user}:code      HMAC of the code, expires after `ttl`
- cooldown: opsdesk:otp:{purpose}:{user}:cooldown  set when a code is sent; no new code until it expires (`resend`)
- tries:    opsdesk:otp:{purpose}:{user}:tries     wrong codes in the current window; at `max_tries` the code is
            dropped and nothing verifies until the window (`ttl`) ends. A new code does not reset it, so asking for
            fresh codes never buys more guesses.

The mail is sent in the request, not through the task queue: the code never sits in the broker, and a delivery failure
is reported to the person straight away (the stored code is discarded so they can ask again at once).

Store outage: fail closed (`StoreUnavailable`), like the sign-in throttle. This module never logs a code (the
development-only console mail backend prints the whole mail, code included).
"""
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass

import redis

from ..services import mailer

logger = logging.getLogger("opsdesk.email_otp")

KEY_PREFIX = "opsdesk:otp"
LOGIN, ENABLE, DISABLE = "login", "enable", "disable"
UNAVAILABLE = "Codes can't be checked right now. Try again shortly."
NOT_SENT = "We couldn't send the code to your email. Try again shortly."

_SUBJECTS = {LOGIN: "Your OpsDesk sign-in code",
             ENABLE: "Confirm turning on two-step sign-in",
             DISABLE: "Confirm turning off two-step sign-in"}
_PURPOSE_LINES = {LOGIN: "Use this code to finish signing in to OpsDesk:",
                  ENABLE: "Use this code to turn on two-step sign-in for your OpsDesk account:",
                  DISABLE: "Use this code to turn off two-step sign-in for your OpsDesk account:"}


def _int(name, default):
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default  # a zero or negative value never disables the control


@dataclass(frozen=True)
class Limits:
    ttl: int = _int("OPSDESK_OTP_TTL", 600)
    resend: int = _int("OPSDESK_OTP_RESEND", 60)
    max_tries: int = _int("OPSDESK_OTP_MAX_TRIES", 5)


CONFIG = Limits()


class StoreUnavailable(Exception):
    pass


class DeliveryFailed(Exception):
    pass


def keys(purpose, user_id):
    base = f"{KEY_PREFIX}:{purpose}:{int(user_id)}"
    return {"code": f"{base}:code", "cooldown": f"{base}:cooldown", "tries": f"{base}:tries"}


def _digest(secret, purpose, user_id, code):
    return hmac.new(secret.encode(), f"{purpose}:{int(user_id)}:{code}".encode(), hashlib.sha256).hexdigest()


def mask_email(email):
    """`sreegousalya.v@corp.example` -> `s***@corp.example`: enough to recognise the mailbox, not to learn it."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if local and domain else "your email"


def issue(client, secret, purpose, user_id, config=None):
    """A new code, replacing any earlier one; None while the resend cooldown from the last code still holds."""
    config = config or CONFIG
    k = keys(purpose, user_id)
    try:
        if not client.set(k["cooldown"], "1", nx=True, ex=config.resend):
            return None
        code = f"{secrets.randbelow(10 ** 6):06d}"
        client.set(k["code"], _digest(secret, purpose, user_id, code), ex=config.ttl)
        return code
    except redis.RedisError as exc:
        logger.error("email code store unavailable: %s", type(exc).__name__)
        raise StoreUnavailable() from exc


def verify(client, secret, purpose, user_id, code, config=None):
    """True once for the current code. A wrong code is counted; too many drop the code for the rest of the window."""
    config = config or CONFIG
    k = keys(purpose, user_id)
    code = str(code or "").strip()
    try:
        if int(client.get(k["tries"]) or 0) >= config.max_tries:
            return False
        stored = client.get(k["code"])
        if stored and len(code) == 6 and code.isdigit() and hmac.compare_digest(
                stored, _digest(secret, purpose, user_id, code)):
            if not client.delete(k["code"]):  # another request used it first
                return False
            client.delete(k["tries"], k["cooldown"])
            return True
        pipe = client.pipeline(transaction=True)
        pipe.incr(k["tries"])
        pipe.expire(k["tries"], config.ttl, nx=True)
        tries = pipe.execute()[0]
        if tries >= config.max_tries:
            client.delete(k["code"])
            logger.warning("email code dropped after too many wrong tries: purpose=%s", purpose)
        return False
    except redis.RedisError as exc:
        logger.error("email code store unavailable: %s", type(exc).__name__)
        raise StoreUnavailable() from exc


def discard(client, purpose, user_id):
    """Forget the current code and its cooldown (the mail could not be sent, so the person may ask again at once)."""
    k = keys(purpose, user_id)
    try:
        client.delete(k["code"], k["cooldown"])
    except redis.RedisError as exc:
        logger.error("email code discard failed: %s", type(exc).__name__)


def send(client, secret, purpose, user_id, email, config=None):
    """Issue a code and mail it. True when a mail went out, False when one was sent moments ago (cooldown).
    Raises DeliveryFailed when the mail could not be handed to the mail server."""
    config = config or CONFIG
    code = issue(client, secret, purpose, user_id, config)
    if code is None:
        return False
    minutes = max(1, config.ttl // 60)
    body = (f"{_PURPOSE_LINES[purpose]}\n\n    {code}\n\n"
            f"The code works once and expires in {minutes} minute{'' if minutes == 1 else 's'}.\n"
            "If you didn't ask for it, someone may know your password: change it and tell your administrator.\n")
    try:
        mailer.send_mail(_SUBJECTS[purpose], body, [email])
    except Exception as exc:  # noqa: BLE001 - any delivery failure is reported the same way
        logger.error("email code delivery failed: purpose=%s error=%s", purpose, type(exc).__name__)
        discard(client, purpose, user_id)
        raise DeliveryFailed() from exc
    return True
