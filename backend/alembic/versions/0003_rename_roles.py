"""Rename the staff roles and remove the requester-only employee role.

    admin    -> manager
    manager  -> team_lead
    agent    -> employee
    employee -> removed: those users are deactivated (kept as least-privileged ``customer`` rows so history stays
                intact) and nobody inherits their access.

The same mapping is applied to the ``allowed_roles`` lists of workflow transitions. Audit-log history is append-only
and keeps the words that were true when it was written.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_UP = {"admin": "manager", "manager": "team_lead", "agent": "employee"}
_DOWN = {"manager": "admin", "team_lead": "manager", "employee": "agent"}


def _values(mapping):
    return ", ".join(f"('{old}', '{new}')" for old, new in mapping.items())


def _remap_users(up: bool) -> None:
    mapping = _UP if up else _DOWN
    case = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items())
    deactivate = "is_active = CASE WHEN role = 'employee' THEN false ELSE is_active END," if up else ""
    # one statement, so the renames cannot collide with each other
    sets_role = f"role = CASE role {case} WHEN 'employee' THEN 'customer' ELSE role END" if up else f"role = CASE role {case} ELSE role END"
    new_staff = "('manager', 'team_lead')" if up else "('admin', 'manager')"
    op.execute(f"UPDATE core_user SET {deactivate} {sets_role}")
    op.execute(f"UPDATE core_user SET is_staff = (role IN {new_staff}) WHERE role <> 'customer' OR is_staff")


def _remap_transitions(up: bool) -> None:
    mapping = _UP if up else _DOWN
    op.execute(f"""
        UPDATE workflow_transition t SET allowed_roles = COALESCE((
            SELECT jsonb_agg(m.new_role ORDER BY e.ord)
            FROM jsonb_array_elements_text(t.allowed_roles) WITH ORDINALITY AS e(v, ord)
            JOIN (VALUES {_values(mapping)}) AS m(old_role, new_role) ON m.old_role = e.v), '[]'::jsonb)
        WHERE jsonb_typeof(t.allowed_roles) = 'array' AND jsonb_array_length(t.allowed_roles) > 0
    """)


def _run(up: bool) -> None:
    bind = op.get_bind()
    # workflow_transition is FORCE row-level-security: lift it for this transaction, ``rls.apply`` restores it.
    op.execute("ALTER TABLE workflow_transition NO FORCE ROW LEVEL SECURITY")
    _remap_users(up)
    _remap_transitions(up)
    from opsdesk.db import rls
    rls.apply(bind)


def upgrade() -> None:
    _run(True)


def downgrade() -> None:
    _run(False)
