"""The super admin role: for now it has exactly the access of a manager (and may be extended later)."""
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from opsdesk.api import admin_rules as rules
from opsdesk.api import audit_view
from opsdesk.cli import app as cli_app
from opsdesk.db.models import Tenant, User
from opsdesk.db.models.core import PRIVILEGED_ROLES, ROLES, STAFF_ROLES, TOP_ROLES, effective_roles
from opsdesk.db.session import plain_session, tenant_session
from opsdesk.services import workflow
from tests.conftest import World, create_ticket_api, make_world

pytestmark = pytest.mark.usefixtures("wide_admin_access")

A = "/api/admin"
ALL_ROLES = ("super_admin", "manager", "team_lead", "employee", "customer")


@pytest.fixture
def sworld() -> World:
    return make_world(roles=ALL_ROLES)


def etag(r):
    return r.headers["etag"]


class TestRoleDefinitions:
    def test_super_admin_is_a_staff_role_with_manager_level_groups(self):
        assert "super_admin" in ROLES and "super_admin" in STAFF_ROLES
        assert set(TOP_ROLES) == {"super_admin", "manager"}
        assert set(PRIVILEGED_ROLES) == {"super_admin", "manager", "team_lead"}
        # the pure-Python rule module mirrors the model constants
        assert rules.ROLES == ROLES and rules.STAFF_ROLES == STAFF_ROLES
        assert rules.TOP_ROLES == TOP_ROLES and rules.PRIVILEGED_ROLES == PRIVILEGED_ROLES

    def test_rules_treat_super_admin_exactly_like_manager(self):
        for target in ("employee", "customer", "team_lead", "manager"):
            assert rules.user_permissions("super_admin", 1, target, 2) == rules.user_permissions("manager", 1, target, 2)
        assert rules.can_read_users("super_admin") and rules.can_manage_teams("super_admin") and rules.can_manage_sla("super_admin")
        assert audit_view.can_read("super_admin")
        assert rules.staff_flag("super_admin", False) is True

    def test_a_team_lead_may_not_touch_a_super_admin(self):
        assert rules.user_permissions("team_lead", 1, "super_admin", 2)["editable_fields"] == []
        with pytest.raises(rules.RuleError) as e:
            rules.check_user_update("team_lead", 1, "super_admin", 2, {"job_title"})
        assert e.value.status == 403

    def test_super_admin_counts_as_manager_for_workflow_transitions(self):
        assert effective_roles("super_admin") == ("super_admin", "manager") and effective_roles("employee") == ("employee",)

        class T:
            customer_allowed = False
            allowed_roles = ["manager"]

        class U:
            def __init__(self, role):
                self.role, self.is_agent = role, role != "customer"

        assert workflow._role_ok(T, U("super_admin")) and workflow._role_ok(T, U("manager"))
        assert not workflow._role_ok(T, U("team_lead")) and not workflow._role_ok(T, U("employee"))


class TestAccessMatchesManager:
    ENDPOINTS = ["/api/auth/me/", "/api/tickets/", "/api/catalog/", "/api/reports/overview/", "/api/channels/", f"{A}/config/",
                 f"{A}/config/workflows/", f"{A}/users/", f"{A}/teams/", f"{A}/sla-policies/", f"{A}/business-hours/", f"{A}/audit-logs/"]

    def test_same_status_as_manager_on_every_area(self, api, sworld, auth):
        for path in self.ENDPOINTS:
            sa = api.get(path, headers=auth("super_admin", sworld)).status_code
            mg = api.get(path, headers=auth("manager", sworld)).status_code
            assert sa == mg == 200, (path, sa, mg)

    def test_sees_every_ticket(self, api, sworld, auth):
        create_ticket_api(api, auth("customer", sworld), "one")
        create_ticket_api(api, auth("employee", sworld), "two")
        counts = {r: api.get("/api/tickets/", headers=auth(r, sworld)).json()["count"] for r in ("super_admin", "manager")}
        assert counts["super_admin"] == counts["manager"] == 2

    def test_me_reports_the_role_and_staff_status(self, api, sworld, auth):
        body = api.get("/api/auth/me/", headers=auth("super_admin", sworld)).json()
        assert body["role"] == "super_admin" and body["is_agent"] is True

    def test_available_transitions_match_a_manager(self, api, sworld, auth):
        he = auth("employee", sworld)
        t = create_ticket_api(api, he, "root cause", type_key="problem")

        def detail(h):
            return api.get(f"/api/tickets/{t['id']}/", headers=h).json()

        def move(name, **fields):
            tr = next(x for x in detail(he)["transitions"] if x["name"] == name)
            body = {"transition_id": tr["id"], **({"fields": fields} if fields else {})}
            assert api.post(f"/api/tickets/{t['id']}/transition/", headers=he, json=body).status_code == 200

        move("Investigate")
        move("Resolve", root_cause="config drift", resolution_notes="fixed")
        names = lambda h: [x["name"] for x in detail(h)["transitions"]]  # noqa: E731
        # "Close" is limited to team_lead and manager: a super admin counts as a manager, an employee does not
        assert "Close" in names(auth("manager", sworld)) and "Close" in names(auth("super_admin", sworld))
        assert "Close" not in names(he)
        close = next(x for x in detail(auth("super_admin", sworld))["transitions"] if x["name"] == "Close")
        r = api.post(f"/api/tickets/{t['id']}/transition/", headers=auth("super_admin", sworld), json={"transition_id": close["id"]})
        assert r.status_code == 200


class TestManagerOnlyActions:
    def test_super_admin_sets_passwords_changes_roles_and_deactivates(self, api, sworld, auth):
        h = auth("super_admin", sworld)
        uid = sworld.users["customer"]
        assert api.post(f"{A}/users/{uid}/password/", headers=h, json={"password": "Brand-New-Passw0rd!"}).json() == {"ok": True}
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        r = api.patch(f"{A}/users/{uid}/", headers={**h, "If-Match": v}, json={"role": "employee"})
        assert r.status_code == 200 and r.json()["role"] == "employee"
        with tenant_session(sworld.tenant_id) as s:
            assert s.get(User, uid).is_staff is False
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        assert api.post(f"{A}/users/{uid}/deactivate/", headers={**h, "If-Match": v}).status_code == 200

    def test_manager_deactivation_by_super_admin_is_allowed(self, api, sworld, auth):
        h = auth("super_admin", sworld)
        mid = sworld.users["manager"]
        v = etag(api.get(f"{A}/users/{mid}/", headers=h))
        assert api.post(f"{A}/users/{mid}/deactivate/", headers={**h, "If-Match": v}).status_code == 200


class TestSuperAdminIsLocked:
    """One account for the whole site; no role (the super admin included) creates, edits, deactivates or resets it."""

    @pytest.mark.parametrize("actor", ["super_admin", "manager", "team_lead"])
    def test_no_role_can_change_the_super_admin_through_the_admin_apis(self, api, sworld, auth, actor):
        h, sid = auth(actor, sworld), sworld.users["super_admin"]
        r = api.get(f"{A}/users/{sid}/", headers=h)
        assert r.status_code == 200  # viewing is allowed; changing is not
        perms = r.json()["permissions"]
        assert perms["editable_fields"] == [] and not perms["can_change_role"] and not perms["can_deactivate"]
        v = etag(r)
        hv = {**h, "If-Match": v}
        assert api.patch(f"{A}/users/{sid}/", headers=hv, json={"job_title": "x"}).status_code == 403
        assert api.patch(f"{A}/users/{sid}/", headers=hv, json={"role": "employee"}).status_code == 403
        assert api.post(f"{A}/users/{sid}/deactivate/", headers=hv).status_code == 403
        assert api.post(f"{A}/users/{sid}/reactivate/", headers=hv).status_code == 403
        assert api.post(f"{A}/users/{sid}/password/", headers=h, json={"password": "Brand-New-Passw0rd!"}).status_code == 403
        assert api.post(f"{A}/users/{sid}/mfa/disable/", headers=h).status_code == 403
        with tenant_session(sworld.tenant_id) as s:
            u = s.get(User, sid)
            assert u.role == "super_admin" and u.is_active and u.job_title == ""

    @pytest.mark.parametrize("actor", ["super_admin", "manager"])
    def test_nobody_can_assign_the_super_admin_role(self, api, sworld, auth, actor):
        h = auth(actor, sworld)
        uid = sworld.users["employee"]
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        r = api.patch(f"{A}/users/{uid}/", headers={**h, "If-Match": v}, json={"role": "super_admin"})
        assert r.status_code == 403
        with tenant_session(sworld.tenant_id) as s:
            assert s.get(User, uid).role == "employee"

    def test_the_database_allows_only_one_super_admin(self, sworld):
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            with tenant_session(sworld.tenant_id) as s:
                s.get(User, sworld.users["manager"]).role = "super_admin"

    def test_the_super_admin_still_manages_everyone_else(self, api, sworld, auth):
        h = auth("super_admin", sworld)
        uid = sworld.users["team_lead"]
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        assert api.patch(f"{A}/users/{uid}/", headers={**h, "If-Match": v}, json={"job_title": "Lead"}).status_code == 200

    def test_recovery_is_an_operator_task_and_works(self):
        run = CliRunner().invoke
        run(cli_app, ["create-tenant", "--name", "Rec Corp", "--slug", "rec-corp", "--admin-email", "root@rec.test", "--admin-password", "Xk9!vQ2#mLw8pZ-cli"])
        assert run(cli_app, ["create-user", "--tenant", "rec-corp", "--email", "boss@rec.test", "--role", "super_admin", "--password", "Xk9!vQ2#mLw8pZ-cli"]).exit_code == 0
        assert run(cli_app, ["reset-password", "--email", "boss@rec.test", "--password", "Another!Passw0rd-9"]).exit_code == 0


class TestCli:
    def test_create_user_accepts_the_role_and_sets_the_staff_flag(self):
        run = CliRunner().invoke
        run(cli_app, ["create-tenant", "--name", "Sa Corp", "--slug", "sa-corp", "--admin-email", "root@sa.test", "--admin-password", "Xk9!vQ2#mLw8pZ-cli"])
        r = run(cli_app, ["create-user", "--tenant", "sa-corp", "--email", "boss@sa.test", "--role", "super_admin", "--password", "Xk9!vQ2#mLw8pZ-cli"])
        assert r.exit_code == 0, r.output
        second = run(cli_app, ["create-user", "--tenant", "sa-corp", "--email", "second@sa.test", "--role", "super_admin", "--password", "Xk9!vQ2#mLw8pZ-cli"])
        assert second.exit_code == 1 and "already exists" in second.output
        with plain_session() as ps:
            tid = ps.scalars(select(Tenant.id).where(Tenant.slug == "sa-corp")).one()
        with tenant_session(tid) as s:
            u = s.scalars(select(User).where(User.email == "boss@sa.test")).one()
            assert u.role == "super_admin" and u.is_staff is True and u.is_agent is True
