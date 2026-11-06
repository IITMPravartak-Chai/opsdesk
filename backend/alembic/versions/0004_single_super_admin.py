"""At most one super admin on the whole site.

A partial unique index on ``core_user (role) WHERE role = 'super_admin'`` makes a second super admin impossible
whatever code path tries to create or promote one. If the database already holds several, this revision fails
loudly: decide which one stays (``UPDATE core_user SET role = 'manager' WHERE ...``) and run the upgrade again.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("core_user_single_super_admin", "core_user", ["role"], unique=True,
                    postgresql_where="role = 'super_admin'")


def downgrade() -> None:
    op.drop_index("core_user_single_super_admin", table_name="core_user")
