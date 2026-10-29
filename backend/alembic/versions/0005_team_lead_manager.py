"""A team can name its team lead and its manager.

Two nullable references from ``core_team`` to ``core_user`` (set to NULL when that user is deleted). Additive: existing
teams simply have neither. The API only accepts an active user holding the matching role.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("team_lead_id", "manager_id"):
        op.add_column("core_team", sa.Column(col, sa.BigInteger(), nullable=True))
        op.create_foreign_key(op.f(f"core_team_{col}_fk"), "core_team", "core_user", [col], ["id"], ondelete="SET NULL")
        op.create_index(op.f(f"core_team_{col}_idx"), "core_team", [col], unique=False)


def downgrade() -> None:
    for col in ("manager_id", "team_lead_id"):
        op.drop_index(op.f(f"core_team_{col}_idx"), table_name="core_team")
        op.drop_constraint(op.f(f"core_team_{col}_fk"), "core_team", type_="foreignkey")
        op.drop_column("core_team", col)
