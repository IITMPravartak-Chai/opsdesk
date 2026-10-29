"""Signed tenant context: claim minting, validation and Redis replay protection.

Two layers of defence :
1. Application: every query is scoped by tenant in the services, and each signed context may be
   claimed exactly once (atomic ``SET NX EX`` in a shared Redis).
2. Database: PostgreSQL row-level security evaluates ``public.get_verified_tenant_id()``, which checks the
   HMAC-SHA256 signature over ``v1:<tenant>:<timestamp>:<key_id>:<nonce>`` and its freshness.

Replay keys have the form ``opsdesk:replay:v1:<tid>:<kid>:<nonce>``.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Tuple

import redis

from .errors import (InvalidTenantContextError, ReplayDetectedError, ReplayServiceUnavailableError, SecurityError)
from .settings import settings

logger = logging.getLogger(__name__)

KEY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
TENANT_ID_REGEX = re.compile(r"^[1-9][0-9]{0,18}$")
TIMESTAMP_REGEX = re.compile(r"^[1-9][0-9]{9}$")
NONCE_REGEX = re.compile(r"^[a-f0-9]{32}$")
SIGNATURE_REGEX = re.compile(r"^[a-f0-9]{64}$")
MAX_64BIT_INT = 9223372036854775807

REPLAY_KEY_PREFIX = "opsdesk:replay"
REPLAY_PROTOCOL_VERSION = "v1"

MAX_AGE_SECONDS = 60
MAX_FUTURE_SKEW_SECONDS = 5

CLAIM_FIELDS = ("tenant_id", "timestamp", "key_id", "nonce", "signature")


# --------------------------------------------------------------------------- replay store
class _LocalReplayStore:
    """In-process replay store for development and tests (``USE_LOCAL_CACHE=1``). Never used in production."""

    def __init__(self) -> None:
        self._items: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_nx(self, key: str, ttl: int) -> bool:
        now = time.monotonic()
        with self._lock:
            for k in [k for k, exp in self._items.items() if exp <= now]:
                del self._items[k]
            if key in self._items:
                return False
            self._items[key] = now + ttl
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_local_store = _LocalReplayStore()
_redis_client: redis.Redis | None = None
_redis_url: str | None = None
_redis_lock = threading.Lock()


def get_redis() -> redis.Redis:
    """Shared Redis client with a connection pool. Fails closed if REDIS_URL is missing."""
    global _redis_client, _redis_url
    url = settings.redis_url
    if not url:
        logger.error("REDIS_URL is not configured for tenant replay protection.")
        raise ReplayServiceUnavailableError("Replay protection store configuration missing.")
    with _redis_lock:
        if _redis_client is None or _redis_url != url:
            _redis_client = redis.Redis(
                connection_pool=redis.ConnectionPool.from_url(
                    url, max_connections=50, socket_connect_timeout=settings.replay_timeout,
                    socket_timeout=settings.replay_timeout, decode_responses=True))
            _redis_url = url
    return _redis_client


def reset_local_replay_store() -> None:
    _local_store.clear()


# --------------------------------------------------------------------------- claims
def get_signing_config() -> Tuple[str, bytes]:
    return settings.signing_config()


def get_replay_cache_key(tenant_id: int | str, key_id: str, nonce: str) -> str:
    return f"{REPLAY_KEY_PREFIX}:{REPLAY_PROTOCOL_VERSION}:{tenant_id}:{key_id}:{nonce}"


def sign_tenant_context(tenant_id: int | str, key_id: str | None = None, secret_bytes: bytes | None = None) -> dict:
    """Mint canonical signed tenant claims (ARCH-06 protocol)."""
    if key_id is None or secret_bytes is None:
        cfg_id, cfg_secret = get_signing_config()
        key_id = key_id or cfg_id
        secret_bytes = secret_bytes or cfg_secret
    t_id = str(tenant_id)
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)  # 32 lowercase hex chars
    msg = f"v1:{t_id}:{ts}:{key_id}:{nonce}".encode("utf-8")
    return {
        "tenant_id": t_id,
        "timestamp": ts,
        "key_id": key_id,
        "nonce": nonce,
        "signature": hmac.new(secret_bytes, msg, hashlib.sha256).hexdigest(),
    }


def validate_signed_claims(claims: dict) -> Tuple[int, str, str, str, str]:
    """Validate schema, freshness and HMAC. Returns (tenant_id, timestamp, key_id, nonce, signature)."""
    if not isinstance(claims, dict):
        raise InvalidTenantContextError("Claims payload must be a dictionary.")
    for k in CLAIM_FIELDS:
        if k not in claims or not isinstance(claims[k], str):
            raise InvalidTenantContextError(f"Missing or invalid claims field: {k}")

    t_id, ts, kid, nonce, sig = (claims[k] for k in CLAIM_FIELDS)
    if not (1 <= len(t_id) <= 19 and len(ts) == 10 and 1 <= len(kid) <= 32 and len(nonce) == 32 and len(sig) == 64):
        raise InvalidTenantContextError("Claims field length bounds violation.")
    if not (TENANT_ID_REGEX.match(t_id) and TIMESTAMP_REGEX.match(ts) and KEY_ID_REGEX.match(kid)
            and NONCE_REGEX.match(nonce) and SIGNATURE_REGEX.match(sig)):
        raise InvalidTenantContextError("Claims field regex format validation failed.")
    if len(t_id) == 19 and t_id > str(MAX_64BIT_INT):
        raise InvalidTenantContextError("Tenant ID exceeds 64-bit signed integer maximum.")

    now = int(time.time())
    req = int(ts)
    if req - now > MAX_FUTURE_SKEW_SECONDS:
        raise InvalidTenantContextError("Claims timestamp is in the future beyond allowed 5s clock skew.")
    if now - req > MAX_AGE_SECONDS:
        raise InvalidTenantContextError("Claims timestamp is expired (> 60s past age).")

    cfg_id, cfg_secret = get_signing_config()
    if kid != cfg_id:
        raise InvalidTenantContextError(f"Key ID '{kid}' does not match active signing key.")
    expected = hmac.new(cfg_secret, f"v1:{t_id}:{ts}:{kid}:{nonce}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise InvalidTenantContextError("HMAC-SHA256 signature verification failed.")
    return int(t_id), ts, kid, nonce, sig


def claim_signed_context(claims: dict) -> bool:
    """Validate and atomically claim signed claims. Fails closed (403 replay/invalid, 503 store down)."""
    t_id, ts, kid, nonce, _sig = validate_signed_claims(claims)
    key = get_replay_cache_key(t_id, kid, nonce)
    ttl = settings.replay_ttl
    try:
        if settings.use_local_cache:
            acquired = _local_store.set_nx(key, ttl)
        else:
            acquired = get_redis().set(key, ts, nx=True, ex=ttl)
    except ReplayServiceUnavailableError:
        raise
    except (redis.ConnectionError, redis.TimeoutError, redis.RedisError) as exc:
        logger.error("Replay protection store unreachable for tenant_id=%s, key_id=%s: %s", t_id, kid, type(exc).__name__)
        raise ReplayServiceUnavailableError() from exc
    except Exception as exc:  # noqa: BLE001 - any store failure must fail closed
        logger.error("Unexpected replay protection error for tenant_id=%s, key_id=%s: %s", t_id, kid, type(exc).__name__)
        raise ReplayServiceUnavailableError("Replay protection failure.") from exc
    if not acquired:
        logger.warning("Replay detected: nonce=%s... for tenant_id=%s, key_id=%s", nonce[:8], t_id, kid)
        raise ReplayDetectedError()
    return True


def mint_claims(tenant_id: int | str) -> dict:
    """Sign and claim a fresh context for ``tenant_id`` (one replay slot)."""
    if tenant_id is None:
        raise InvalidTenantContextError("tenant_id is required.")
    claims = sign_tenant_context(tenant_id)
    claim_signed_context(claims)
    return claims


def claims_are_fresh(claims: dict | None, margin: int = 15) -> bool:
    """True while ``claims`` can still pass the database freshness window with ``margin`` seconds to spare."""
    if not claims:
        return False
    try:
        age = int(time.time()) - int(claims["timestamp"])
    except (KeyError, ValueError, TypeError):
        return False
    return -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_AGE_SECONDS - margin


SET_CLAIMS_SQL = (
    "SELECT set_config('app.tenant_id', %s, true), set_config('app.tenant_timestamp', %s, true), "
    "set_config('app.tenant_key_id', %s, true), set_config('app.tenant_nonce', %s, true), "
    "set_config('app.tenant_signature', %s, true)"
)


def claim_params(claims: dict) -> tuple:
    return tuple(str(claims[k]) for k in CLAIM_FIELDS)


def system_context():
    """Discontinued RLS bypass. Cross-tenant work iterates tenants with ``tenant_session(tenant_id)`` or uses
    the SECURITY DEFINER lookup functions."""
    raise SecurityError(
        "system_context() RLS bypass has been discontinued under cryptographic signed tenant isolation. "
        "Use authorized SECURITY DEFINER functions or iterate tenants using tenant_session(tenant_id)."
    )
