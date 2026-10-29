"""Migration 0003 (role rename) maps users and workflow transitions, in both directions, and deactivates the removed role.

Everything runs in one transaction that is rolled back, so other tests' data is untouched."""
import importlib.util

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from opsdesk.cli import BACKEND_DIR
from opsdesk.db.session import get_owner_engine


def _migration():
    path = BACKEND_DIR / "alembic" / "versions" / "0003_rename_roles.py"
    spec = importlib.util.spec_from_file_location("rename_roles_0003", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _roles(conn, world):
    rows = conn.execute(text("SELECT id, role, is_staff, is_active FROM core_user WHERE tenant_id = :t"), {"t": world.tenant_id}).all()
    return {r.id: (r.role, r.is_staff, r.is_active) for r in rows}


def _cancel_roles(conn, world):
    for table in ("workflow_transition", "workflow_workflow"):  # forced row-level security hides rows from the owner
        conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    rows = conn.execute(text("SELECT t.allowed_roles FROM workflow_transition t JOIN workflow_workflow w ON w.id = t.workflow_id "
                             "WHERE w.tenant_id = :t AND t.name = 'Cancel' AND w.name = 'Incident'"), {"t": world.tenant_id}).all()
    return rows[0][0]


def test_roles_are_renamed_both_ways_and_the_requester_role_is_deactivated(world):
    mod = _migration()
    u = world.users
    with get_owner_engine().connect() as conn:
        tx = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                assert _cancel_roles(conn, world) == ["employee", "team_lead", "manager"]

                mod.downgrade()  # new names -> the previous names
                before = _roles(conn, world)
                assert before[u["manager"]][:2] == ("admin", True) and before[u["team_lead"]][:2] == ("manager", True)
                assert before[u["employee"]][:2] == ("agent", False) and before[u["customer"]][0] == "customer"
                assert _cancel_roles(conn, world) == ["agent", "manager", "admin"]

                # a user of the removed requester role (the old "employee") exists in the old data
                conn.execute(text("UPDATE core_user SET role = 'employee' WHERE id = :i"), {"i": u["customer"]})

                mod.upgrade()  # previous names -> new names
                after = _roles(conn, world)
                assert after[u["manager"]] == ("manager", True, True)
                assert after[u["team_lead"]] == ("team_lead", True, True)
                assert after[u["employee"]] == ("employee", False, True)
                assert after[u["customer"]] == ("customer", False, False)  # the removed role: deactivated, no staff access
                assert _cancel_roles(conn, world) == ["employee", "team_lead", "manager"]
        finally:
            tx.rollback()
