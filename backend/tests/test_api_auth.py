"""Sign-in, tokens, MFA, role re-validation and rate limiting through the HTTP API."""
import pyotp
import pytest
from sqlalchemy import select

from opsdesk.db.models import AuditLog, User
from opsdesk.db.session import tenant_session
from opsdesk.security import tokens
from tests.conftest import World, login, make_world


def post_login(api, email, password, **extra):
    return api.post("/api/auth/login/", json={"email": email, "password": password, **extra})


class TestLogin:
    def test_success_returns_tokens_and_profile(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD)
        body = r.json()
        assert r.status_code == 200 and body["user"]["role"] == "employee" and body["user"]["is_agent"] is True
        assert tokens.decode_access(body["access"])["tid"] == world.tenant_id
        assert tokens.decode_refresh(body["refresh"])["user_id"] == world.users["employee"]

    def test_token_alias_route_and_username_field_work(self, api, world):
        assert api.post("/api/auth/token/", json={"email": world.email("employee"), "password": World.PASSWORD}).status_code == 200
        assert api.post("/api/auth/login/", json={"username": world.email("employee"), "password": World.PASSWORD}).status_code == 200

    def test_email_is_case_insensitive(self, api, world):
        assert post_login(api, world.email("employee").upper(), World.PASSWORD).status_code == 200

    @pytest.mark.parametrize("email,pw", [("nobody@nowhere.example", "whatever-long-enough"), ("", ""), ("x", "")])
    def test_unknown_account_and_blank_credentials_fail_uniformly(self, api, world, email, pw):
        r = post_login(api, email, pw)
        assert r.status_code == 401 and r.json()["detail"] == "Email or password is incorrect."

    def test_wrong_password_matches_unknown_account_response(self, api, world):
        a = post_login(api, world.email("employee"), "wrong-password-here")
        b = post_login(api, "ghost@nowhere.example", "wrong-password-here")
        assert (a.status_code, a.json()) == (b.status_code, b.json())

    def test_inactive_user_and_inactive_tenant_cannot_sign_in(self, api, world):
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).is_active = False
        assert post_login(api, world.email("employee"), World.PASSWORD).status_code == 401
        from opsdesk.db.models import Tenant
        from opsdesk.db.session import plain_session
        with plain_session() as s:
            s.get(Tenant, world.tenant_id).is_active = False
        assert post_login(api, world.email("team_lead"), World.PASSWORD).status_code == 401

    def test_successful_login_is_audited_and_old_hash_is_upgraded(self, api, world):
        from opsdesk.security import passwords
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).password = passwords.make_password(World.PASSWORD, iterations=1000)
        assert post_login(api, world.email("employee"), World.PASSWORD).status_code == 200
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(AuditLog).where(AuditLog.action == "auth.login")).first() is not None
            assert passwords.check_password(World.PASSWORD, s.get(User, world.users["employee"]).password)


class TestMfa:
    def _enable(self, api, w, role="employee"):
        h = login(api, w, role)
        secret = api.post("/api/auth/mfa/", headers=h, json={}).json()["secret"]
        r = api.post("/api/auth/mfa/", headers=h, json={"code": pyotp.TOTP(secret).now()})
        assert r.json() == {"mfa_enabled": True, "otpauth_uri": None, "secret": None} or r.json().get("mfa_enabled") is True
        return secret

    def test_enrolment_then_login_requires_the_code(self, api, world):
        secret = self._enable(api, world)
        r = post_login(api, world.email("employee"), World.PASSWORD)
        assert r.status_code == 401 and r.json()["mfa_required"] is True
        bad = post_login(api, world.email("employee"), World.PASSWORD, otp="000000")
        assert bad.status_code == 401 and bad.json()["mfa_required"] is True
        ok = post_login(api, world.email("employee"), World.PASSWORD, otp=pyotp.TOTP(secret).now())
        assert ok.status_code == 200

    def test_wrong_code_enrolment_is_refused_and_reenrolment_while_on_is_blocked(self, api, world):
        h = login(api, world, "team_lead")
        api.post("/api/auth/mfa/", headers=h, json={})
        assert api.post("/api/auth/mfa/", headers=h, json={"code": "123456"}).status_code == 400
        secret = self._enable(api, world, "manager")
        assert api.post("/api/auth/mfa/", headers=login(api, world, "manager") if False else {}, json={}).status_code == 401
        h2 = {"Authorization": "Bearer " + post_login(api, world.email("manager"), World.PASSWORD, otp=pyotp.TOTP(secret).now()).json()["access"]}
        assert api.post("/api/auth/mfa/", headers=h2, json={}).status_code == 409


class TestRefreshAndRevalidation:
    def test_refresh_yields_a_working_access_token(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        a = api.post("/api/auth/refresh/", json={"refresh": r["refresh"]})
        assert a.status_code == 200
        me = api.get("/api/auth/me/", headers={"Authorization": f"Bearer {a.json()['access']}"})
        assert me.status_code == 200 and me.json()["email"] == world.email("employee")

    def test_garbage_and_access_tokens_cannot_refresh(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        for bad in ("junk", r["access"], ""):
            assert api.post("/api/auth/refresh/", json={"refresh": bad}).status_code == 401

    def test_refresh_rotates_and_revokes_the_old_token(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        a = api.post("/api/auth/refresh/", json={"refresh": r["refresh"]})
        assert a.status_code == 200 and a.json()["refresh"] and a.json()["refresh"] != r["refresh"]
        assert api.post("/api/auth/refresh/", json={"refresh": r["refresh"]}).status_code == 401   # replay
        assert api.post("/api/auth/refresh/", json={"refresh": a.json()["refresh"]}).status_code == 200

    def test_logout_revokes_the_refresh_token(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        assert api.post("/api/auth/logout/", json={"refresh": r["refresh"]}).status_code == 204
        assert api.post("/api/auth/refresh/", json={"refresh": r["refresh"]}).status_code == 401
        assert api.post("/api/auth/logout/", json={"refresh": r["refresh"]}).status_code == 204   # idempotent
        assert api.post("/api/auth/logout/", json={"refresh": "junk"}).status_code == 204

    def test_revoked_refresh_token_is_refused(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        tokens.blacklist(tokens.decode_refresh(r["refresh"]))
        assert api.post("/api/auth/refresh/", json={"refresh": r["refresh"]}).status_code == 401

    def test_refresh_carries_the_current_role_not_the_login_role(self, api, world):
        r = post_login(api, world.email("employee"), World.PASSWORD).json()
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).role = "team_lead"
        access = api.post("/api/auth/refresh/", json={"refresh": r["refresh"]}).json()["access"]
        assert tokens.decode_access(access)["role"] == "team_lead"

    def test_deactivated_user_loses_access_immediately(self, api, world):
        h = login(api, world, "employee")
        assert api.get("/api/auth/me/", headers=h).status_code == 200
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).is_active = False
        assert api.get("/api/auth/me/", headers=h).status_code == 401
        assert api.get("/api/tickets/", headers=h).status_code == 401

    def test_demoted_agent_is_refused_on_staff_routes(self, api, world):
        h = login(api, world, "employee")
        assert api.get("/api/reports/overview/", headers=h).status_code == 200
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).role = "customer"
        assert api.get("/api/reports/overview/", headers=h).status_code == 403

    def test_token_for_another_tenant_claim_is_rejected(self, api, world, other_world):
        # a token claiming this user belongs to a different tenant must not authenticate
        refresh, claims = tokens.issue_refresh(world.users["employee"], other_world.tenant_id, "employee")
        access = tokens.issue_access_from(claims)
        assert api.get("/api/auth/me/", headers={"Authorization": f"Bearer {access}"}).status_code == 401

    @pytest.mark.parametrize("header", [None, "", "Bearer", "Basic abc", "Bearer a b", "Bearer not.a.jwt"])
    def test_missing_or_malformed_authorization(self, api, header):
        h = {"Authorization": header} if header is not None else {}
        assert api.get("/api/auth/me/", headers=h).status_code == 401


class TestProfile:
    def test_me_for_every_role(self, api, world):
        for role in ("manager", "team_lead", "employee", "customer"):
            r = api.get("/api/auth/me/", headers=login(api, world, role))
            assert r.status_code == 200 and r.json()["role"] == role

    def test_agents_can_toggle_availability_customers_cannot(self, api, world):
        h = login(api, world, "employee")
        assert api.patch("/api/auth/me/", headers=h, json={"is_available": False}).json()["is_available"] is False
        hc = login(api, world, "customer")
        assert api.patch("/api/auth/me/", headers=hc, json={"is_available": False}).status_code == 403

    def test_meta_is_scoped_by_role(self, api, world):
        agent = api.get("/api/meta/", headers=login(api, world, "employee")).json()
        customer = api.get("/api/meta/", headers=login(api, world, "customer")).json()
        assert agent["teams"] is not None and agent["states"] and len(agent["accounts"]) == 1
        assert customer["teams"] is None and customer["agents"] is None and [a["code"] for a in customer["accounts"]] == ["GLBX"]
        assert {t["key"] for t in customer["ticket_types"]} == {"incident", "service_request"}  # customer_can_create only


class TestLoginThrottle:
    def test_account_limit_returns_429_with_retry_after(self, api, world):
        for _ in range(10):
            assert post_login(api, world.email("employee"), "wrong-password-here").status_code == 401
        r = post_login(api, world.email("employee"), "wrong-password-here")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert "Too many" in r.json()["detail"]

    def test_unknown_accounts_are_throttled_identically(self, api):
        for _ in range(10):
            post_login(api, "ghost@nowhere.example", "wrong-password-here")
        assert post_login(api, "ghost@nowhere.example", "wrong-password-here").status_code == 429

    def test_a_successful_login_clears_the_account_counter(self, api, world):
        for _ in range(5):
            post_login(api, world.email("employee"), "wrong-password-here")
        assert post_login(api, world.email("employee"), World.PASSWORD).status_code == 200
        for _ in range(9):
            assert post_login(api, world.email("employee"), "wrong-password-here").status_code == 401


class TestMetaTeams:
    def test_each_team_lists_its_active_staff_members_for_the_assign_dialog(self, api, world, auth):
        from opsdesk.db.models import Team, TeamMember, User
        from opsdesk.db.session import tenant_session
        with tenant_session(world.tenant_id) as s:
            t = Team(tenant_id=world.tenant_id, name="Desk")
            s.add(t)
            s.flush()
            s.add_all([TeamMember(team_id=t.id, user_id=world.users["employee"]), TeamMember(team_id=t.id, user_id=world.users["customer"])])
            team_id = t.id
        teams = {x["id"]: x for x in api.get("/api/meta/", headers=auth("employee")).json()["teams"]}
        assert teams[team_id]["member_ids"] == [world.users["employee"]]  # a customer is never offered
        with tenant_session(world.tenant_id) as s:
            s.get(User, world.users["employee"]).is_active = False
        teams = {x["id"]: x for x in api.get("/api/meta/", headers=auth("manager")).json()["teams"]}
        assert teams[team_id]["member_ids"] == []  # deactivated people drop out
        assert api.get("/api/meta/", headers=auth("customer")).json()["teams"] is None
