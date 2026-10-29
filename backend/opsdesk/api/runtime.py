"""Bridge between async handlers and the synchronous service layer."""
from __future__ import annotations

from typing import Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..db.models import User
from ..db.session import tenant_session
from .security import AuthenticatedUser

T = TypeVar("T")


async def run_in_tenant(user_or_tenant, fn: Callable[[Session], T], *, commit: bool = True) -> T:
    """Run ``fn(session)`` in a worker thread inside a tenant-bound session (one transaction; committed on success,
    rolled back if ``fn`` raises, after-commit hooks run after the commit)."""
    tenant_id = user_or_tenant.tenant_id if hasattr(user_or_tenant, "tenant_id") else int(user_or_tenant)

    def _run() -> T:
        with tenant_session(tenant_id, commit=commit) as s:
            return fn(s)

    return await run_in_threadpool(_run)


def db_user(session: Session, auth: AuthenticatedUser, *, active: bool = True) -> User | None:
    """The caller's own ORM row in their tenant."""
    stmt = select(User).where(User.id == auth.user_id, User.tenant_id == auth.tenant_id)
    if active:
        stmt = stmt.where(User.is_active.is_(True))
    return session.scalars(stmt).first()
