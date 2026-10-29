"""Transaction-local Row-Level Security (RLS) tenant context manager for FastAPI.

Ensures every protected database operation executes within an explicit transaction
where PostgreSQL tenant GUCs are set with is_local=true, guaranteeing that:
1. GUCs cannot leak to subsequent requests or pooled connections.
2. PostgreSQL public.get_verified_tenant_id() verifies the cryptographic signature.
3. Cancellation and exceptions trigger immediate rollback and context clearing.
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import psycopg

from .db import db_manager
from .. import tenancy
from .security import AuthenticatedUser

logger = logging.getLogger(__name__)


@asynccontextmanager
async def tenant_transaction(
    user: AuthenticatedUser,
    claims: Optional[dict] = None,
) -> AsyncIterator[psycopg.AsyncConnection]:
    """Execute within an explicitly managed, transaction-local tenant context.

    Acquires a pooled connection, opens an explicit transaction, applies signed
    tenant GUCs locally (is_local=true), and releases the connection safely.
    """
    if claims is None:
        claims = tenancy.mint_claims(user.tenant_id)

    async with db_manager.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT
                            set_config('app.tenant_id', %s, true),
                            set_config('app.tenant_timestamp', %s, true),
                            set_config('app.tenant_key_id', %s, true),
                            set_config('app.tenant_nonce', %s, true),
                            set_config('app.tenant_signature', %s, true)
                        """,
                        (
                            str(claims["tenant_id"]),
                            str(claims["timestamp"]),
                            str(claims["key_id"]),
                            str(claims["nonce"]),
                            str(claims["signature"]),
                        ),
                    )
                yield conn
        finally:
            # Transaction end (commit/rollback) automatically clears transaction-local settings.
            # Defensive check: ensure no active transaction remains on pooled connection.
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                try:
                    await conn.rollback()
                except Exception:
                    pass
