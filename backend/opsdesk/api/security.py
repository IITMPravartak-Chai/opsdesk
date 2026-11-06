"""Authentication and authorization dependencies for the API.

Validates the access token, re-reads the caller's role and active flag from the database on every request (a
claim can be up to a refresh lifetime old), and enforces staff/manager gates. Tenant signing and replay
protection live in ``opsdesk.tenancy``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import redis
from fastapi import Header, HTTPException, status
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ..db.models import User
from ..db.session import tenant_session
from ..security.tokens import MAX_64BIT_INT, TokenError, decode_access, normalize_user_id  # noqa: F401
from ..settings import settings

logger = logging.getLogger(__name__)

STAFF_ROLES = ("super_admin", "manager", "team_lead", "employee")
TOP_ROLES = ("super_admin", "manager")
PRIVILEGED_ROLES = ("super_admin", "manager", "team_lead")
ADMIN_ROLES = ("super_admin",)  # the Administration area: users, teams, SLA, configuration, audit log

_redis_client: Optional[redis.Redis] = None
_normalize_user_id = normalize_user_id


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int
    tenant_id: int
    role: str

    @property
    def is_agent(self) -> bool:
        return self.role in STAFF_ROLES


def get_redis_client() -> redis.Redis:
    """Shared Redis client (login throttle, OAuth code guard) with pooling and timeouts."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.redis_url, socket_timeout=settings.redis_timeout,
                                             socket_connect_timeout=settings.redis_timeout, decode_responses=True)
    return _redis_client


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def decode_access_token(token: str) -> dict:
    try:
        return decode_access(token)
    except TokenError as exc:
        raise _unauthorized(str(exc))


def load_current_role(user_id: int, tenant_id: int) -> Optional[str]:
    """The role stored on the user's active row in the claimed tenant, or None (sync; call in a thread)."""
    with tenant_session(tenant_id, commit=False) as s:
        return s.scalars(select(User.role).where(User.id == user_id, User.tenant_id == tenant_id,
                                                 User.is_active.is_(True))).first()


async def current_role(user_id: int, tenant_id: int) -> str:
    """Authorization uses the role and active flag as they are now, not as they were when the token was issued.
    A deactivated, deleted or other-tenant user is refused with 401."""
    role = await run_in_threadpool(load_current_role, user_id, tenant_id)
    if not role:
        raise _unauthorized("User is inactive or not found.")
    return role


def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise _unauthorized("Authentication credentials were not provided.")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthorized("Authorization header must be in format 'Bearer <token>'.")
    return parts[1]


def _claims(payload: dict) -> tuple[int, int, object]:
    user_id, tenant_id, role = payload.get("user_id"), payload.get("tid"), payload.get("role")
    if user_id is None or not isinstance(user_id, int):
        raise _unauthorized("Token missing valid user_id claim.")
    if tenant_id is None or not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id <= 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="User has no assigned tenant or invalid tenant claim.")
    return user_id, tenant_id, role


FORBIDDEN = "You do not have permission to perform this action."


async def get_current_user(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    """Authenticate the caller and enforce staff role authorization."""
    payload = decode_access_token(_bearer(authorization))
    user_id, tenant_id, role = _claims(payload)
    if not role or role not in STAFF_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    role = await current_role(user_id, tenant_id)
    if role not in STAFF_ROLES:  # demoted since the token was issued
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    return AuthenticatedUser(user_id=user_id, tenant_id=tenant_id, role=role)


async def get_authenticated_user(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    """Recipient-scoped routes (notifications, event stream, meta, profile): every signed-in role may use them for
    its *own* data. Routes using it must scope every query to the caller's user_id and tenant_id."""
    payload = decode_access_token(_bearer(authorization))
    user_id, tenant_id, role = _claims(payload)
    if not role or not isinstance(role, str):
        raise _unauthorized("Token missing valid role claim.")
    return AuthenticatedUser(user_id=user_id, tenant_id=tenant_id, role=await current_role(user_id, tenant_id))


async def get_manager(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    user = await get_current_user(authorization)
    if user.role not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    return user


async def require_administration(authorization: Optional[str] = Header(None)) -> None:
    """Router-level guard for every Administration endpoint: only the super admin gets in."""
    user = await get_current_user(authorization)
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administration is only available to the super admin.")


async def get_admin(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    user = await get_current_user(authorization)
    if user.role not in TOP_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    return user
