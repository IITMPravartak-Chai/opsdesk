"""Refresh-token revocation table.

Revoked refresh-token ids (``jti``) live here until they would have expired anyway; the ``purge_revoked_tokens``
task removes expired rows.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_blacklistedtoken",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti", name="auth_blacklistedtoken_pkey"),
    )
    op.create_index("auth_blacklistedtoken_expires_at_idx", "auth_blacklistedtoken", ["expires_at"])

    from opsdesk.db import rls
    rls.apply(op.get_bind())  # grants on the new table for the app role; policies are idempotent


def downgrade() -> None:
    op.drop_index("auth_blacklistedtoken_expires_at_idx", table_name="auth_blacklistedtoken")
    op.drop_table("auth_blacklistedtoken")
