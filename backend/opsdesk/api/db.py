"""Database connection and pool management for FastAPI CMDB application.

Uses psycopg_pool.AsyncConnectionPool for direct typed asynchronous SQL access.
Ensures cancellation safety and proper connection release.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import psycopg
from psycopg_pool import AsyncConnectionPool

from .config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages the lifecycle of the async PostgreSQL connection pool."""

    def __init__(self):
        self._pool: Optional[AsyncConnectionPool] = None

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    async def open(self, conninfo: Optional[str] = None):
        """Open the asynchronous database pool."""
        if self._pool is not None:
            return
        uri = conninfo or settings.get_conninfo()
        self._pool = AsyncConnectionPool(
            conninfo=uri,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout=settings.db_pool_timeout,
            open=False,
        )
        await self._pool.open()
        logger.info("FastAPI PostgreSQL async connection pool initialized.")

    async def close(self):
        """Close the connection pool cleanly."""
        if self._pool is not None:
            pool = self._pool
            self._pool = None
            try:
                await pool.close(timeout=2.0)
            except Exception as exc:
                logger.debug("Database connection pool close encountered: %s", exc)
            logger.info("FastAPI PostgreSQL async connection pool closed.")

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        """Acquire a connection from the pool within an async context."""
        if self._pool is None:
            raise RuntimeError("Database connection pool is not initialized.")
        async with self._pool.connection() as conn:
            yield conn

    async def ping(self) -> bool:
        """Execute a minimal health check query without logging sensitive data."""
        if self._pool is None:
            return False
        try:
            async with self.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    row = await cur.fetchone()
                    return row is not None and row[0] == 1
        except Exception as exc:
            logger.warning("FastAPI database health check query failed: %s", type(exc).__name__)
            return False


db_manager = DatabaseManager()
