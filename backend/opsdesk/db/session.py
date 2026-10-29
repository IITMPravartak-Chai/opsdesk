"""Engines and tenant-scoped sessions.

Every session that touches tenant data is opened through ``tenant_session`` (sync: Celery, CLI, tests) or
``async_tenant_session`` (FastAPI). They apply the signed tenant claims as **transaction-local** settings
(``set_config(..., true)``) every time a transaction begins, so nothing can leak to the next user of a
pooled connection, and a mid-request commit followed by new work is still covered. Claims are re-minted
when they are about to leave the database's 60 second freshness window.

Never connect with the schema owner role: owners bypass RLS. ``DATABASE_URL`` must be the app role.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from .. import tenancy
from ..settings import settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def _connect_args() -> dict:
    return {"sslmode": "require"} if settings.db_require_ssl else {}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.sqlalchemy_url, pool_pre_ping=True, pool_size=settings.db_pool_min_size,
            max_overflow=max(settings.db_pool_max_size - settings.db_pool_min_size, 0),
            pool_timeout=settings.db_pool_timeout, connect_args=_connect_args())
    return _engine


_owner_engine: Engine | None = None


def get_owner_engine() -> Engine:
    """Engine for the schema owner (MIGRATION_DATABASE_URL): used by migrations and the few maintenance tasks that must
    bypass the app role's limits (demo reset). Never use it for request handling: owners bypass RLS."""
    global _owner_engine
    if _owner_engine is None:
        _owner_engine = create_engine(settings.migration_sqlalchemy_url, pool_pre_ping=True, connect_args=_connect_args())
    return _owner_engine


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            settings.sqlalchemy_url, pool_pre_ping=True, pool_size=settings.db_pool_min_size,
            max_overflow=max(settings.db_pool_max_size - settings.db_pool_min_size, 0),
            pool_timeout=settings.db_pool_timeout, connect_args=_connect_args())
    return _async_engine


def dispose_engines() -> None:
    global _engine, _async_engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _async_engine = None  # disposed by the owner of the event loop via ``await dispose_async_engine()``


async def dispose_async_engine() -> None:
    global _async_engine
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None


# --------------------------------------------------------------------------- transaction-local claims
TENANT_KEY = "opsdesk_tenant_id"
CLAIMS_KEY = "opsdesk_claims"


@event.listens_for(Session, "after_begin")
def _apply_tenant_claims(session: Session, transaction, connection) -> None:
    tenant_id = session.info.get(TENANT_KEY)
    if tenant_id is None:
        return
    claims = session.info.get(CLAIMS_KEY)
    if not tenancy.claims_are_fresh(claims):
        claims = tenancy.mint_claims(tenant_id)
        session.info[CLAIMS_KEY] = claims
    connection.exec_driver_sql(tenancy.SET_CLAIMS_SQL, tenancy.claim_params(claims))


def _bind_tenant(session: Session, tenant_id: int, claims: dict | None = None) -> None:
    if tenant_id is None or int(tenant_id) <= 0:
        raise tenancy.InvalidTenantContextError("A positive tenant_id is required.")
    session.info[TENANT_KEY] = int(tenant_id)
    session.info[CLAIMS_KEY] = claims


@contextmanager
def tenant_session(tenant_id: int, *, commit: bool = True) -> Iterator[Session]:
    """Sync session whose every transaction runs under the signed context of ``tenant_id``."""
    claims = tenancy.mint_claims(tenant_id)  # fail closed before any connection is taken
    session = Session(get_engine(), expire_on_commit=False)
    _bind_tenant(session, tenant_id, claims)
    try:
        yield session
        if commit:
            session.commit()
        else:
            session.rollback()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def async_tenant_session(tenant_id: int, *, commit: bool = True) -> AsyncIterator[AsyncSession]:
    """Async session for FastAPI handlers, same guarantees as ``tenant_session``."""
    claims = await asyncio.to_thread(tenancy.mint_claims, tenant_id)
    session = AsyncSession(get_async_engine(), expire_on_commit=False)
    _bind_tenant(session.sync_session, tenant_id, claims)
    try:
        yield session
        if commit:
            await session.commit()
        else:
            await session.rollback()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextmanager
def plain_session() -> Iterator[Session]:
    """Session with **no** tenant context (RLS returns nothing). Only for the SECURITY DEFINER lookups used
    before a tenant is known: login, inbound-token and CSAT-token resolution."""
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def async_plain_session() -> AsyncIterator[AsyncSession]:
    session = AsyncSession(get_async_engine(), expire_on_commit=False)
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            return conn.execute(text("SELECT 1")).scalar() == 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database health check failed: %s", type(exc).__name__)
        return False
