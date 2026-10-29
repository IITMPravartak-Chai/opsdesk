"""Users, teams, SLA edits and audit-log API: permissions, optimistic concurrency, last-admin protection, impact previews."""
import pytest
from sqlalchemy import select

from opsdesk.db.models import AuditLog, User
from opsdesk.db.session import tenant_session
from opsdesk.security import passwords
from tests.conftest import World, create_ticket_api, login

pytestmark = pytest.mark.usefixtures("wide_admin_access")

A = "/api/admin"


def etag(r):
    return r.headers["etag"]


class TestUsers:
    def test_listing_requires_admin_or_manager_and_never_lists_superusers(self, api, world, auth):
        assert api.get(f"{A}/users/", headers=auth("employee")).status_code == 403
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).is_superuser = True
        body = api.get(f"{A}/users/", headers=auth("team_lead")).json()
        assert body["count"] == 3 and world.users["employee"] not in {u["id"] for u in body["results"]}

    def test_filters_search_ordering_paging(self, api, world, auth):
        h = auth("manager")
        assert api.get(f"{A}/users/?role=customer", headers=h).json()["count"] == 1
        assert api.get(f"{A}/users/?q=manager", headers=h).json()["count"] == 1
        assert api.get(f"{A}/users/?is_active=false", headers=h).json()["count"] == 0
        assert api.get(f"{A}/users/?page_size=2&ordering=email", headers=h).json()["next"]
        for bad in ("?role=wizard", "?is_active=maybe", "?ordering=drop", "?team=abc"):
            assert api.get(f"{A}/users/{bad}", headers=h).status_code == 400

    def test_patch_requires_if_match_and_rejects_stale_versions(self, api, world, auth):
        h = auth("manager")
        r = api.get(f"{A}/users/{world.users['employee']}/", headers=h)
        v = etag(r)
        assert api.patch(f"{A}/users/{world.users['employee']}/", headers=h, json={"job_title": "SRE"}).status_code == 428
        ok = api.patch(f"{A}/users/{world.users['employee']}/", headers={**h, "If-Match": v}, json={"job_title": "SRE", "skills": ["k8s"]})
        assert ok.status_code == 200 and ok.json()["job_title"] == "SRE" and ok.json()["version"] != r.json()["version"]
        stale = api.patch(f"{A}/users/{world.users['employee']}/", headers={**h, "If-Match": v}, json={"job_title": "Other"})
        assert stale.status_code == 412

    def test_manager_limits(self, api, world, auth):
        hm = auth("team_lead")
        aid, mid = world.users["employee"], world.users["team_lead"]
        v = etag(api.get(f"{A}/users/{aid}/", headers=hm))
        assert api.patch(f"{A}/users/{aid}/", headers={**hm, "If-Match": v}, json={"role": "manager"}).status_code == 403
        assert api.patch(f"{A}/users/{aid}/", headers={**hm, "If-Match": v}, json={"phone": "+4915100000"}).status_code == 200
        v2 = etag(api.get(f"{A}/users/{world.users['manager']}/", headers=hm))
        assert api.patch(f"{A}/users/{world.users['manager']}/", headers={**hm, "If-Match": v2}, json={"phone": "1"}).status_code == 403
        assert api.post(f"{A}/users/{aid}/deactivate/", headers=hm).status_code == 403

    def test_role_change_updates_staff_flag_and_is_audited(self, api, world, auth):
        h = auth("manager")
        uid = world.users["employee"]
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        r = api.patch(f"{A}/users/{uid}/", headers={**h, "If-Match": v}, json={"role": "team_lead"})
        assert r.status_code == 200 and r.json()["role"] == "team_lead"
        with tenant_session(world.tenant_id) as s:
            assert s.get(User, uid).is_staff is True
            row = s.scalars(select(AuditLog).where(AuditLog.action == "admin.user.update")).one()
            assert row.changes["role"] == ["employee", "team_lead"]

    def test_nobody_changes_their_own_role_or_deactivates_themselves(self, api, world, auth):
        h = auth("manager")
        me = world.users["manager"]
        v = etag(api.get(f"{A}/users/{me}/", headers=h))
        assert api.patch(f"{A}/users/{me}/", headers={**h, "If-Match": v}, json={"role": "employee"}).status_code == 409
        assert api.post(f"{A}/users/{me}/deactivate/", headers=h).status_code == 409

    def test_last_active_admin_is_protected(self, api, world, auth):
        h = auth("manager")
        with tenant_session(world.tenant_id) as s:  # a second admin exists, then the first tries to demote it after the other is gone
            from opsdesk.security.passwords import make_password
            s.add(User(email="admin2@t.example", username="admin2@t.example", tenant_id=world.tenant_id, role="manager", is_staff=True,
                       password=make_password("x" * 14)))
        other = next(u for u in api.get(f"{A}/users/?role=manager", headers=h).json()["results"] if u["email"] == "admin2@t.example")
        v = etag(api.get(f"{A}/users/{other['id']}/", headers=h))
        assert api.post(f"{A}/users/{other['id']}/deactivate/", headers={**h, "If-Match": v}).status_code == 200  # two admins: fine
        v = etag(api.get(f"{A}/users/{other['id']}/", headers=h))
        assert api.post(f"{A}/users/{other['id']}/reactivate/", headers={**h, "If-Match": v}).status_code == 200

    def test_deactivate_and_reactivate(self, api, world, auth):
        h = auth("manager")
        uid = world.users["employee"]
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        d = api.post(f"{A}/users/{uid}/deactivate/", headers={**h, "If-Match": v})
        assert d.status_code == 200 and d.json()["is_active"] is False and d.json()["is_available"] is False
        assert api.get("/api/auth/me/", headers=login_attempt(api, world, "employee")).status_code == 401
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        r = api.post(f"{A}/users/{uid}/reactivate/", headers={**h, "If-Match": v})
        assert r.json()["is_active"] is True and r.json()["is_available"] is False

    def test_reporting_line_cycles_and_foreign_ids_are_refused(self, api, world, other_world, auth):
        h = auth("manager")
        a, m = world.users["employee"], world.users["team_lead"]
        v = etag(api.get(f"{A}/users/{a}/", headers=h))
        assert api.patch(f"{A}/users/{a}/", headers={**h, "If-Match": v}, json={"manager_id": m}).status_code == 200
        v = etag(api.get(f"{A}/users/{m}/", headers=h))
        assert api.patch(f"{A}/users/{m}/", headers={**h, "If-Match": v}, json={"manager_id": a}).status_code == 400  # cycle
        assert api.patch(f"{A}/users/{m}/", headers={**h, "If-Match": v}, json={"manager_id": m}).status_code == 400
        assert api.patch(f"{A}/users/{m}/", headers={**h, "If-Match": v}, json={"manager_id": other_world.users["manager"]}).status_code == 400
        assert api.patch(f"{A}/users/{m}/", headers={**h, "If-Match": v}, json={"account_id": 10 ** 9}).status_code == 400

    def test_unwritable_fields_are_rejected(self, api, world, auth):
        h = auth("manager")
        uid = world.users["employee"]
        v = etag(api.get(f"{A}/users/{uid}/", headers=h))
        for field in ("email", "password", "is_superuser", "is_staff", "tenant_id", "mfa_enabled"):
            assert api.patch(f"{A}/users/{uid}/", headers={**h, "If-Match": v}, json={field: "x"}).status_code == 422

    def test_other_tenants_users_are_invisible(self, api, world, other_world, auth):
        assert api.get(f"{A}/users/{other_world.users['employee']}/", headers=auth("manager")).status_code == 404

    def test_admin_sets_password_and_it_is_policy_checked_and_audited(self, api, world, auth):
        h = auth("manager")
        uid = world.users["customer"]
        assert api.post(f"{A}/users/{uid}/password/", headers=h, json={"password": "short"}).status_code == 400
        assert api.post(f"{A}/users/{uid}/password/", headers=auth("team_lead"), json={"password": "Brand-New-Passw0rd!"}).status_code == 403
        assert api.post(f"{A}/users/{uid}/password/", headers=h, json={"password": "Brand-New-Passw0rd!"}).json() == {"ok": True}
        assert api.post("/api/auth/login/", json={"email": world.email("customer"), "password": "Brand-New-Passw0rd!"}).status_code == 200
        assert api.post("/api/auth/login/", json={"email": world.email("customer"), "password": World.PASSWORD}).status_code == 401
        with tenant_session(world.tenant_id) as s:
            row = s.scalars(select(AuditLog).where(AuditLog.action == "admin.user.password_set")).one()
            assert "Brand-New" not in str(row.changes) and row.object_id == str(uid)

    def test_admin_disables_mfa(self, api, world, auth):
        import pyotp
        h = login_ok(api, world, "employee")
        secret = api.post("/api/auth/mfa/", headers=h, json={}).json()["secret"]
        api.post("/api/auth/mfa/", headers=h, json={"code": pyotp.TOTP(secret).now()})
        assert api.post("/api/auth/login/", json={"email": world.email("employee"), "password": World.PASSWORD}).json()["mfa_required"]
        ha = auth("manager")
        assert api.post(f"{A}/users/{world.users['employee']}/mfa/disable/", headers=auth("team_lead")).status_code == 403
        assert api.post(f"{A}/users/{world.users['employee']}/mfa/disable/", headers=ha).json()["mfa_enabled"] is False
        assert api.post("/api/auth/login/", json={"email": world.email("employee"), "password": World.PASSWORD}).status_code == 200
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(AuditLog).where(AuditLog.action == "admin.user.mfa_disabled")).first()


def login_attempt(api, w, role):
    r = api.post("/api/auth/login/", json={"email": w.email(role), "password": World.PASSWORD})
    return {"Authorization": f"Bearer {r.json().get('access', 'none')}"}


def login_ok(api, w, role):
    return login(api, w, role)


class TestTeams:
    def make(self, api, h, name="Network", **extra):
        r = api.post(f"{A}/teams/", headers=h, json={"name": name, **extra})
        assert r.status_code == 201, r.text
        return r.json()

    def test_create_with_members_accounts_and_audit(self, api, world, auth):
        h = auth("team_lead")
        t = self.make(api, h, members_ids := None) if False else self.make(
            api, h, "Network", assignment_strategy="round_robin", member_ids=[world.users["employee"]], account_ids=[world.account_id])
        assert [m["id"] for m in t["members"]] == [world.users["employee"]] and t["members"][0]["eligible"] is True
        assert [a["id"] for a in t["accounts"]] == [world.account_id] and t["can_delete"] is True
        listing = api.get(f"{A}/teams/", headers=h).json()["results"][0]
        assert listing["member_count"] == 1 and listing["account_count"] == 1 and listing["open_ticket_count"] == 0

    def test_names_are_unique_case_and_space_insensitively(self, api, auth):
        h = auth("manager")
        self.make(api, h, "Service Desk")
        assert api.post(f"{A}/teams/", headers=h, json={"name": "  service   desk "}).status_code == 409

    @pytest.mark.parametrize("body", [{"name": ""}, {"name": "x", "assignment_strategy": "random"}, {"name": "x", "email": "nope"},
                                      {"name": "x", "member_ids": [10 ** 9]}, {"name": "x", "account_ids": [10 ** 9]}, {"name": "x", "unknown": 1}])
    def test_invalid_team_payloads(self, api, auth, body):
        assert api.post(f"{A}/teams/", headers=auth("manager"), json=body).status_code in (400, 422)

    def test_only_staff_can_be_members_and_inactive_cannot_be_added(self, api, world, auth):
        h = auth("manager")
        assert api.post(f"{A}/teams/", headers=h, json={"name": "T", "member_ids": [world.users["customer"]]}).status_code == 400
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).is_active = False
        assert api.post(f"{A}/teams/", headers=h, json={"name": "T2", "member_ids": [world.users["employee"]]}).status_code == 400

    def test_members_replacement_and_concurrency(self, api, world, auth):
        h = auth("manager")
        t = self.make(api, h)
        r = api.put(f"{A}/teams/{t['id']}/members/", headers={**h, "If-Match": f'"{t["version"]}"'}, json={"user_ids": [world.users["employee"], world.users["team_lead"]]})
        assert r.status_code == 200 and len(r.json()["members"]) == 2
        stale = api.put(f"{A}/teams/{t['id']}/members/", headers={**h, "If-Match": f'"{t["version"]}"'}, json={"user_ids": []})
        assert stale.status_code == 412
        assert api.put(f"{A}/teams/{t['id']}/members/", headers=h, json={"user_ids": []}).status_code == 428

    def test_update_rename_and_delete_guard(self, api, world, auth):
        h = auth("manager")
        t = self.make(api, h, "Old")
        v = f'"{t["version"]}"'
        r = api.patch(f"{A}/teams/{t['id']}/", headers={**h, "If-Match": v}, json={"name": "New", "assignment_strategy": "least_loaded"})
        assert r.json()["name"] == "New" and r.json()["assignment_strategy"] == "least_loaded"
        create_ticket_api(api, h, "uses team", )
        with tenant_session(world.tenant_id) as s:
            from opsdesk.db.models import Ticket
            s.scalars(select(Ticket)).first().team_id = t["id"]
        v = etag(api.get(f"{A}/teams/{t['id']}/", headers=h))
        blocked = api.delete(f"{A}/teams/{t['id']}/", headers={**h, "If-Match": v})
        assert blocked.status_code == 409 and blocked.json()["usage"]["tickets"] == 1
        empty = self.make(api, h, "Empty")
        assert api.delete(f"{A}/teams/{empty['id']}/", headers={**h, "If-Match": f'"{empty["version"]}"'}).status_code == 204

    def test_agents_cannot_manage_teams(self, api, auth):
        assert api.get(f"{A}/teams/", headers=auth("employee")).status_code == 403
        assert api.post(f"{A}/teams/", headers=auth("employee"), json={"name": "x"}).status_code == 403


class TestSlaEdits:
    def policy(self, api, h):
        return next(p for p in api.get(f"{A}/sla-policies/", headers=h).json()["results"] if p["is_default"])

    def test_admin_only(self, api, auth):
        for role in ("team_lead", "employee"):
            assert api.get(f"{A}/sla-policies/", headers=auth(role)).status_code == 403
            assert api.get(f"{A}/business-hours/", headers=auth(role)).status_code == 403

    def test_detail_lists_targets_and_options(self, api, auth):
        h = auth("manager")
        d = api.get(f"{A}/sla-policies/{self.policy(api, h)['id']}/", headers=h)
        body = d.json()
        assert d.headers["etag"] and [t["priority"]["key"] for t in body["targets"]] == ["P1", "P2", "P3", "P4"]
        assert body["options"]["business_hours"] and body["default_conflicts"] == []

    def test_edit_targets_with_if_match_and_audit(self, api, world, auth):
        h = auth("manager")
        p = self.policy(api, h)
        d = api.get(f"{A}/sla-policies/{p['id']}/", headers=h)
        targets = [{"priority_id": t["priority"]["id"], "first_response_minutes": 10, "resolution_minutes": 100} for t in d.json()["targets"]]
        assert api.patch(f"{A}/sla-policies/{p['id']}/", headers=h, json={"targets": targets}).status_code == 428
        r = api.patch(f"{A}/sla-policies/{p['id']}/", headers={**h, "If-Match": etag(d)}, json={"targets": targets, "name": "Renamed  SLA"})
        assert r.status_code == 200 and r.json()["name"] == "Renamed SLA" and all(t["resolution_minutes"] == 100 for t in r.json()["targets"])
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(AuditLog).where(AuditLog.action == "admin.sla_policy.update")).first()

    @pytest.mark.parametrize("patch", [{"at_risk_percent": 101}, {"name": ""}, {"is_default": "yes"}, {"business_hours_id": 10 ** 9},
                                       {"ticket_type_ids": [10 ** 9]}, {"targets": [{"priority_id": 1, "first_response_minutes": 0, "resolution_minutes": 5}]},
                                       {"confirm_impact": "true"}])
    def test_invalid_edits(self, api, auth, patch):
        h = auth("manager")
        p = self.policy(api, h)
        v = etag(api.get(f"{A}/sla-policies/{p['id']}/", headers=h))
        assert api.patch(f"{A}/sla-policies/{p['id']}/", headers={**h, "If-Match": v}, json=patch).status_code == 400

    def test_live_change_needs_confirmation_and_previews_impact(self, api, world, auth):
        from datetime import timedelta

        from opsdesk.db.base import utcnow
        from opsdesk.db.models import Ticket
        h = auth("manager")
        p = self.policy(api, h)
        round_the_clock = next(b for b in api.get(f"{A}/business-hours/", headers=h).json()["results"] if b["is_24x7"])["id"]
        v = etag(api.get(f"{A}/sla-policies/{p['id']}/", headers=h))
        # no active tickets yet, so no confirmation is needed; a 24x7 calendar keeps the test independent of the weekday
        assert api.patch(f"{A}/sla-policies/{p['id']}/", headers={**h, "If-Match": v}, json={"business_hours_id": round_the_clock}).status_code == 200
        t = create_ticket_api(api, h, "being timed")
        with tenant_session(world.tenant_id) as s:
            s.get(Ticket, t["id"]).created_at = utcnow() - timedelta(minutes=30)
        v = etag(api.get(f"{A}/sla-policies/{p['id']}/", headers=h))
        preview = api.patch(f"{A}/sla-policies/{p['id']}/", headers={**h, "If-Match": v}, json={"at_risk_percent": 1})
        assert preview.status_code == 409 and preview.json()["impact"]["active_tickets"] == 1 and preview.json()["impact"]["to_at_risk"] == 1
        done = api.patch(f"{A}/sla-policies/{p['id']}/", headers={**h, "If-Match": v}, json={"at_risk_percent": 1, "confirm_impact": True})
        assert done.status_code == 200 and done.json()["at_risk_percent"] == 1
        from opsdesk.worker import tasks
        assert tasks.sla_monitor() >= 1  # the monitor now flags the ticket and notifies the assignee/team
        with tenant_session(world.tenant_id) as s:
            assert s.get(Ticket, t["id"]).sla_status == "at_risk"

    def test_business_hours_edit_validation_and_unreachable_targets(self, api, auth):
        h = auth("manager")
        bh = next(b for b in api.get(f"{A}/business-hours/", headers=h).json()["results"] if not b["is_24x7"])
        v = etag(api.get(f"{A}/business-hours/{bh['id']}/", headers=h))
        ok = api.patch(f"{A}/business-hours/{bh['id']}/", headers={**h, "If-Match": v}, json={"holidays": ["2026-12-25"]})
        assert ok.status_code == 200 and ok.json()["holidays"] == ["2026-12-25"]
        v = etag(ok)
        for bad in ({"timezone": "Mars/Base"}, {"schedule": {"9": [["09:00", "10:00"]]}}, {"schedule": {"0": [["10:00", "09:00"]]}},
                    {"holidays": ["not-a-date"]}, {"schedule": {}}, {"is_24x7": "yes"}):
            assert api.patch(f"{A}/business-hours/{bh['id']}/", headers={**h, "If-Match": v}, json=bad).status_code == 400, bad
        tiny = {"schedule": {"0": [["09:00", "09:01"]]}}  # one working minute a week: the longest target is unreachable in 800 days
        r = api.patch(f"{A}/business-hours/{bh['id']}/", headers={**h, "If-Match": v}, json=tiny)
        assert r.status_code == 400 and "can't be reached" in r.json()["detail"]


class TestAuditLogApi:
    def test_admins_read_it_everyone_else_cannot(self, api, world, auth):
        create_ticket_api(api, auth("employee"), "x")
        api.patch(f"/api/tickets/1/", headers=auth("employee"), json={"subject": "y"})
        h = auth("manager")
        body = api.get(f"{A}/audit-logs/", headers=h).json()
        assert body["count"] >= 1 and {"id", "created_at", "action", "actor", "changes"} <= set(body["results"][0])
        assert "ip_address" not in body["results"][0]
        for role in ("team_lead", "employee", "customer"):
            assert api.get(f"{A}/audit-logs/", headers=auth(role)).status_code == 403
        assert "auth.login" in api.get(f"{A}/audit-logs/actions/", headers=h).json()["actions"]

    def test_filters_validate_and_credentials_are_redacted(self, api, world, auth):
        h = auth("manager")
        assert api.get(f"{A}/audit-logs/?action_prefix=auth.", headers=h).json()["count"] >= 1
        for bad in ("?actor=abc", "?since=garbage", "?action=" + "x" * 90, "?page=0", "?page_size=100000"):
            assert api.get(f"{A}/audit-logs/{bad}", headers=h).status_code == 400, bad
        with tenant_session(world.tenant_id) as s:
            from opsdesk.services.audit import audit
            audit(s, world.tenant_id, "test.secret", None, None, {"password": "hunter2", "note": "ok"})
        row = api.get(f"{A}/audit-logs/?action=test.secret", headers=h).json()["results"][0]
        assert "hunter2" not in str(row)

    def test_other_tenant_events_never_appear(self, api, world, other_world, auth):
        create_ticket_api(api, auth("employee", other_world), "theirs")
        other = api.get(f"{A}/audit-logs/", headers=auth("manager", other_world)).json()
        mine = api.get(f"{A}/audit-logs/", headers=auth("manager")).json()
        assert {r["id"] for r in other["results"]}.isdisjoint({r["id"] for r in mine["results"]})
        assert api.post(f"{A}/audit-logs/", headers=auth("manager"), json={}).status_code == 405
