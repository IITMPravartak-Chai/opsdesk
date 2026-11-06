"""JWT access/refresh tokens.

HS256 tokens with claims ``token_type``,
``exp``, ``iat``, ``jti``, ``user_id`` (decimal string), plus OpsDesk's ``tid`` and ``role``. Access
tokens are minted from a refresh token's claims, exactly as before.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from sqlalchemy import select

from ..settings import settings

MAX_64BIT_INT = 9223372036854775807
USER_ID_REGEX = re.compile(r"[1-9][0-9]{0,18}")  # fullmatch: ASCII digits only, no sign, no leading zero
_NO_COPY = {"token_type", "exp", "iat", "jti"}


class TokenError(Exception):
    """The token is invalid, expired, of the wrong type or has been revoked."""


def normalize_user_id(value: Any) -> Optional[int]:
    """Canonical user_id claim as int, or None. Tokens carry it as a decimal string; an int is also tolerated. Exactly those two forms are accepted (positive, within signed 64-bit range)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= MAX_64BIT_INT else None
    if isinstance(value, str) and USER_ID_REGEX.fullmatch(value) and int(value) <= MAX_64BIT_INT:
        return int(value)
    return None


def _encode(claims: dict) -> str:
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _base(token_type: str, lifetime: timedelta) -> dict:
    now = datetime.now(timezone.utc)
    return {"token_type": token_type, "iat": int(now.timestamp()), "exp": int((now + lifetime).timestamp()),
            "jti": uuid.uuid4().hex}


def issue_refresh(user_id: int, tenant_id: int, role: str) -> tuple[str, dict]:
    claims = _base("refresh", timedelta(hours=settings.jwt_refresh_hours))
    claims.update({"user_id": str(user_id), "tid": int(tenant_id), "role": role})
    return _encode(claims), claims


def issue_access_from(claims: dict, *, role: str | None = None) -> str:
    """Mint an access token carrying the refresh token's claims (optionally with a fresher role)."""
    access = _base("access", timedelta(minutes=settings.jwt_access_minutes))
    access.update({k: v for k, v in claims.items() if k not in _NO_COPY})
    if role is not None:
        access["role"] = role
    return _encode(access)


def decode(token: str, *, token_type: str) -> dict:
    """Verify signature, expiry and type. Raises ``TokenError``; never leaks library detail."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm],
                             options={"require": ["exp", "user_id"]})
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {type(exc).__name__}") from exc
    if payload.get("token_type") != token_type:
        raise TokenError(f"Token is not a {token_type} token.")
    payload["user_id"] = normalize_user_id(payload.get("user_id"))
    return payload


def decode_access(token: str) -> dict:
    return decode(token, token_type="access")


def decode_refresh(token: str) -> dict:
    """Decode a refresh token and refuse one whose jti has been blacklisted."""
    payload = decode(token, token_type="refresh")
    if is_blacklisted(payload.get("jti")):
        raise TokenError("Token is blacklisted.")
    return payload


# --------------------------------------------------------------------------- revocation
def is_blacklisted(jti: str | None) -> bool:
    if not jti:
        return True
    from ..db.models import AuthBlacklistedToken
    from ..db.session import plain_session
    with plain_session() as s:
        return s.scalar(select(AuthBlacklistedToken.jti).where(AuthBlacklistedToken.jti == jti)) is not None


def blacklist(claims: dict) -> None:
    """Revoke a refresh token (idempotent). Rows expire with the token and are purged by the beat job."""
    from sqlalchemy.dialects.postgresql import insert
    from ..db.models import AuthBlacklistedToken
    from ..db.session import plain_session
    exp = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    with plain_session() as s:
        s.execute(insert(AuthBlacklistedToken).values(jti=claims["jti"], expires_at=exp,
                                                     user_id=normalize_user_id(claims.get("user_id")))
                  .on_conflict_do_nothing(index_elements=["jti"]))
