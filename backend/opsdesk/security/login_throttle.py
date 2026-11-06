"""Sign-in rate limiting for POST /api/auth/login/ (and its /api/auth/token/ alias). Pre-U8 security follow-up.

Three fixed-window counters in the shared Redis store (the same store the signed tenant context already requires, so
every FastAPI worker and replica sees the same counts):

- account: attempts per normalised email, `account_limit` per `account_window` seconds. Counted for unknown emails
  exactly as for real ones, so a 429 never tells a caller whether an account exists. Temporary by construction (the
  window expires); there is no permanent lockout. Trade-off: anyone can hold one email's sign-in at 429 for at most
  one window by sending wrong passwords; the source limits below bound how much one source can do that.
- source failures: attempts per client address, `source_limit` per `source_window` seconds (credential stuffing
  across many accounts from one address; set high enough for offices behind one NAT).
- source rate: every request per client address, `rate_limit` per `rate_window` seconds (hammering; also bounds
  password-hashing work).

An attempt is reserved (INCR) before the password is checked, so concurrent requests can never get more than the limit
of password checks through; a correct password, or a correct password waiting for its two-step code, refunds the
reservation (a successful sign-in clears the account counter). A wrong two-step code stays counted, which also limits
guessing of 6-digit codes.

Client address: the direct peer, unless the peer is a trusted proxy (OPSDESK_TRUSTED_PROXIES, CIDRs or exact names;
default: loopback and private networks, where the bundled nginx runs). Then the address `trusted_hops` from the right of
X-Forwarded-For is used; nginx appends the address it saw ($proxy_add_x_forwarded_for), so anything a client writes in
the header sits further left and is ignored. Behind an additional load balancer, set OPSDESK_TRUSTED_PROXY_HOPS=2.

Store outage: fail closed (503), consistent with the replay store; authenticated requests cannot work without it anyway.
Keys hold an HMAC of the email or address (never the value itself, a password or a token); logs hold a key prefix only.
"""
import hashlib
import hmac
import ipaddress
import logging
import os
from dataclasses import dataclass

import redis

logger = logging.getLogger("opsdesk.login_throttle")

KEY_PREFIX = "opsdesk:rl:login"
THROTTLED = "Too many sign-in attempts. Try again in about {minutes} minute{s}."
UNAVAILABLE = "Sign-in is temporarily unavailable. Try again shortly."


def _int(name, default):
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default  # a zero or negative value never disables the control


@dataclass(frozen=True)
class Limits:
    account_limit: int = _int("OPSDESK_LOGIN_ACCOUNT_LIMIT", 10)
    account_window: int = _int("OPSDESK_LOGIN_ACCOUNT_WINDOW", 900)
    source_limit: int = _int("OPSDESK_LOGIN_SOURCE_LIMIT", 50)
    source_window: int = _int("OPSDESK_LOGIN_SOURCE_WINDOW", 900)
    rate_limit: int = _int("OPSDESK_LOGIN_RATE_LIMIT", 30)
    rate_window: int = _int("OPSDESK_LOGIN_RATE_WINDOW", 60)
    trusted_proxies: tuple = tuple(p.strip() for p in os.getenv(
        "OPSDESK_TRUSTED_PROXIES", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16").split(",") if p.strip())
    trusted_hops: int = _int("OPSDESK_TRUSTED_PROXY_HOPS", 1)


CONFIG = Limits()


class Throttled(Exception):
    def __init__(self, retry_after):
        super().__init__("throttled")
        self.retry_after = max(1, int(retry_after))

    @property
    def detail(self):
        minutes = max(1, -(-self.retry_after // 60))
        return THROTTLED.format(minutes=minutes, s="" if minutes == 1 else "s")


class StoreUnavailable(Exception):
    pass


def _is_trusted(peer, trusted):
    for entry in trusted:
        if peer == entry:
            return True
        try:
            if ipaddress.ip_address(peer) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_address(peer, forwarded_for, config=None):
    """The address to limit on. Forwarding headers count only when the direct peer is a trusted proxy."""
    config = config or CONFIG
    peer = (peer or "").strip()
    if peer and _is_trusted(peer, config.trusted_proxies):
        parts = [p.strip() for p in (forwarded_for or "").split(",") if p.strip()]
        if len(parts) >= config.trusted_hops:
            candidate = parts[-config.trusted_hops]
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return peer or "unknown"


def _digest(secret, kind, value):
    return hmac.new(secret.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def keys(secret, email, address):
    """Redis keys for one attempt. Values are keyed hashes, never the email or address."""
    return {"account": f"{KEY_PREFIX}:account:{_digest(secret, 'account', email.strip().lower())}",
            "source": f"{KEY_PREFIX}:source:{_digest(secret, 'source', address)}",
            "rate": f"{KEY_PREFIX}:rate:{_digest(secret, 'rate', address)}"}


class LoginThrottle:
    """One sign-in attempt: `reserve()` before checking the password, then `succeeded()`, `challenged()` or nothing."""

    def __init__(self, client, secret, email, address, config=None):
        self.r, self.config = client, config or CONFIG
        self.keys = keys(secret, email, address)

    def _incr(self, pipe, key, window):
        pipe.incr(key)
        pipe.expire(key, window, nx=True)  # the window starts at the first attempt and is never extended

    def reserve(self):
        c = self.config
        plan = (("rate", c.rate_limit, c.rate_window), ("account", c.account_limit, c.account_window),
                ("source", c.source_limit, c.source_window))
        try:
            pipe = self.r.pipeline(transaction=True)
            for kind, _limit, window in plan:
                self._incr(pipe, self.keys[kind], window)
            for kind, _limit, _window in plan:
                pipe.ttl(self.keys[kind])
            out = pipe.execute()
        except redis.RedisError as exc:
            logger.error("login throttle store unavailable: %s", type(exc).__name__)
            raise StoreUnavailable() from exc
        counts, ttls = out[0:6:2], out[6:9]
        over = [(kind, ttl) for (kind, limit, window), count, ttl in zip(plan, counts, ttls) if count > limit]
        if over:
            kind, ttl = max(over, key=lambda o: o[1])
            logger.warning("login throttled: limit=%s key=%s", kind, self.keys[kind].rsplit(":", 1)[-1][:8])
            raise Throttled(ttl if ttl and ttl > 0 else dict((p[0], p[2]) for p in plan)[kind])

    def _refund(self, kinds):
        try:
            pipe = self.r.pipeline(transaction=True)
            for kind in kinds:
                pipe.decr(self.keys[kind])
            pipe.execute()
        except redis.RedisError as exc:  # a lost refund only makes the limit stricter
            logger.error("login throttle refund failed: %s", type(exc).__name__)

    def succeeded(self):
        """Correct password (and code): clear this account's attempts and give the source its attempt back."""
        try:
            self.r.delete(self.keys["account"])
        except redis.RedisError as exc:
            logger.error("login throttle reset failed: %s", type(exc).__name__)
        self._refund(("source",))

    def challenged(self):
        """Correct password, two-step code still to come: not a failed attempt."""
        self._refund(("account", "source"))
