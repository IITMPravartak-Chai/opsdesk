"""OAuth2 for IMAP integrations (P4-05).

* SASL XOAUTH2 string construction (Google / Microsoft 365 IMAP)
* Tamper-proof, expiring ``state`` tokens (CSRF / callback-abuse protection)
* Token refresh with the channel row locked, tokens encrypted at rest (the channel ``config`` column)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Channel
from ..settings import settings

log = logging.getLogger(__name__)

OAUTH_STATE_SALT = "opsdesk.imap.oauth.state"
DEFAULT_STATE_MAX_AGE = 600  # 10 minutes

PROVIDER_CONFIGS = {
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    },
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "https://mail.google.com/ offline_access",
    },
}


class OAuthStateError(Exception):
    """The state token is missing, forged or expired."""


def build_xoauth2_string(username: str, access_token: str) -> str:
    """SASL XOAUTH2: ``user=<username>\\x01auth=Bearer <token>\\x01\\x01``."""
    if not username or not access_token:
        raise ValueError("Both username and access_token are required for XOAUTH2 authentication.")
    return f"user={username}\x01auth=Bearer {access_token}\x01\x01"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    key = hashlib.sha256((OAUTH_STATE_SALT + settings.jwt_secret_key).encode()).digest()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def generate_oauth_state(channel, user) -> str:
    """Signed state token binding channel, tenant and initiating user, valid for 10 minutes."""
    data = {"channel_id": channel.id, "tenant_id": channel.tenant_id, "user_id": user.id, "ts": int(time.time())}
    payload = _b64(json.dumps(data, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def validate_oauth_state(state_token: str, max_age: int = DEFAULT_STATE_MAX_AGE) -> dict:
    if not state_token or "." not in state_token:
        raise OAuthStateError("Missing state token.")
    payload, _, sig = state_token.partition(".")
    if not hmac.compare_digest(_sign(payload), sig):
        raise OAuthStateError("Bad signature.")
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OAuthStateError("Malformed state.") from exc
    if int(time.time()) - int(data.get("ts", 0)) > max_age:
        raise OAuthStateError("State expired.")
    return data


def get_authorization_url(channel, user, redirect_uri: str, provider: str = "microsoft") -> tuple[str, str]:
    """Provider authorization URL and signed state. Never exposes client secrets or tokens in the URL."""
    cfg = channel.config or {}
    prov_cfg = PROVIDER_CONFIGS.get(provider.lower(), PROVIDER_CONFIGS["microsoft"])
    client_id = cfg.get("client_id")
    if not client_id:
        raise ValueError("Channel configuration must include 'client_id' to initiate OAuth2.")
    state = generate_oauth_state(channel, user)
    params = {
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "response_mode": "query",
        "scope": prov_cfg["scope"], "state": state, "access_type": "offline", "prompt": "consent",
    }
    return f"{prov_cfg['auth_url']}?{urllib.parse.urlencode(params)}", state


def exchange_code_for_tokens(session: Session, channel, code: str, redirect_uri: str, provider: str = "microsoft") -> dict:
    """Exchange an authorization code for tokens and persist them in the (encrypted) channel config."""
    cfg = channel.config or {}
    client_id, client_secret = cfg.get("client_id"), cfg.get("client_secret")
    if not client_id or not client_secret:
        raise ValueError("Channel missing client_id or client_secret.")
    prov_cfg = PROVIDER_CONFIGS.get(provider.lower(), PROVIDER_CONFIGS["microsoft"])
    token_url = cfg.get("token_url") or prov_cfg["token_url"]
    resp = requests.post(token_url, data={
        "client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri}, timeout=10)
    if not resp.ok:
        log.error("OAuth token exchange failed: status=%s", resp.status_code)
        raise RuntimeError(f"Token exchange failed with status {resp.status_code}")

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token") or cfg.get("refresh_token")
    expires_at = int(time.time()) + int(tokens.get("expires_in", 3600))
    new_cfg = {**cfg, "access_token": tokens.get("access_token"), "expires_at": expires_at,
               "auth_type": "oauth2", "oauth_status": "active"}
    if refresh_token:
        new_cfg["refresh_token"] = refresh_token
    channel.config = new_cfg  # assign a new dict: the encrypted column does not track in-place changes
    session.flush()
    return {"status": "active", "expires_at": expires_at, "has_refresh_token": bool(refresh_token)}


def refresh_oauth_token(session: Session, channel) -> bool:
    """Refresh an expired or soon-to-expire access token before a mailbox poll. The channel row is locked so
    concurrent workers cannot race. True if refreshed or still valid; False if the refresh failed."""
    cfg = channel.config or {}
    if cfg.get("auth_type") != "oauth2":
        return True
    if cfg.get("expires_at", 0) - int(time.time()) > 120:  # fast path, no lock
        return True

    locked = session.scalars(select(Channel).where(Channel.id == channel.id).with_for_update()).first()
    if locked is None:
        return False
    session.refresh(locked)
    cfg = dict(locked.config or {})
    if cfg.get("expires_at", 0) - int(time.time()) > 120:  # another worker just refreshed it
        channel.config = cfg
        return True

    refresh_token, client_id, client_secret = cfg.get("refresh_token"), cfg.get("client_id"), cfg.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        log.warning("Channel %s missing OAuth refresh credentials", channel.id)
        locked.config = {**cfg, "oauth_status": "needs_reauthorization"}
        session.flush()
        channel.config = locked.config
        return False

    prov_cfg = PROVIDER_CONFIGS.get(cfg.get("provider", "microsoft").lower(), PROVIDER_CONFIGS["microsoft"])
    token_url = cfg.get("token_url") or prov_cfg["token_url"]
    try:
        resp = requests.post(token_url, data={"client_id": client_id, "client_secret": client_secret,
                                              "grant_type": "refresh_token", "refresh_token": refresh_token}, timeout=10)
        if not resp.ok:
            log.warning("Token refresh failed for channel %s: status=%s", channel.id, resp.status_code)
            locked.config = {**cfg, "oauth_status": "needs_reauthorization"}
            session.flush()
            channel.config = locked.config
            return False
        data = resp.json()
        new_cfg = {**cfg, "access_token": data.get("access_token"),
                   "expires_at": int(time.time()) + int(data.get("expires_in", 3600)), "oauth_status": "active"}
        if data.get("refresh_token"):
            new_cfg["refresh_token"] = data["refresh_token"]
        locked.config = new_cfg
        session.flush()
        channel.config = new_cfg
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Token refresh exception for channel %s: %s", channel.id, type(exc).__name__)
        return False
