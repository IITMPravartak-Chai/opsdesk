"""Sign-in, tokens, two-step sign-in by emailed code, role re-validation and rate limiting through the HTTP API."""
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


def wrong(code):
    """A 6-digit code that is certainly not `code`."""
    return f"{(int(code) + 1) % 10 ** 6:06d}"


def enable_two_step(api, w, box, role="employee"):
    """Turn on two-step sign-in for the role's user through the API, with the code mailed to them."""
    h = login(api, w, role)
    r = api.post("/api/auth/mfa/", headers=h, json={})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is False and r.json()["sent_to"].endswith("@" + w.email(role).split("@")[1])
    r = api.post("/api/auth/mfa/", headers=h, json={"code": box.last(w.email(role))})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is True
    return h


def login_with_code(api, w, box, role="employee"):
    r = post_login(api, w.email(role), World.PASSWORD)
    assert r.status_code == 401 and r.json()["mfa_required"] is True and r.json()["otp_sent"] is True
    r = post_login(api, w.email(role), World.PASSWORD, otp=box.last(w.email(role)))
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access']}"}


class TestMfa:
    def test_turning_it_on_then_login_requires_the_emailed_code(self, api, world, emailed_codes):
        email = world.email("employee")
        enable_two_step(api, world, emailed_codes)
        before = len(emailed_codes.sent)
        r = post_login(api, email, World.PASSWORD)
        assert r.status_code == 401 and r.json()["mfa_required"] is True and r.json()["otp_sent"] is True
        assert len(emailed_codes.sent) == before + 1 and emailed_codes.sent[-1][0] == email
        assert email not in r.json()["detail"] and email[0] + "***@" in r.json()["detail"]   # masked address only
        code = emailed_codes.last(email)
        bad = post_login(api, email, World.PASSWORD, otp=wrong(code))
        assert bad.status_code == 401 and bad.json()["mfa_required"] is True and "otp_sent" not in bad.json()
        assert post_login(api, email, "wrong-password-here", otp=code).status_code == 401   # the code never replaces the password
        ok = post_login(api, email, World.PASSWORD, otp=code)
        assert ok.status_code == 200 and ok.json()["user"]["mfa_enabled"] is True
        assert post_login(api, email, World.PASSWORD, otp=code).status_code == 401        # a code works once

    def test_a_second_request_within_the_cooldown_sends_no_new_mail(self, api, world, emailed_codes):
        email = world.email("employee")
        enable_two_step(api, world, emailed_codes)
        assert post_login(api, email, World.PASSWORD).status_code == 401
        sent, code = len(emailed_codes.sent), emailed_codes.last(email)
        again = post_login(api, email, World.PASSWORD)
        assert again.status_code == 401 and again.json()["mfa_required"] is True and "already sent" in again.json()["detail"]
        assert len(emailed_codes.sent) == sent
        assert post_login(api, email, World.PASSWORD, otp=code).status_code == 200            # the first code still works

    def test_users_without_two_step_get_no_mail_and_no_prompt(self, api, world, emailed_codes):
        assert post_login(api, world.email("employee"), World.PASSWORD).status_code == 200
        assert post_login(api, world.email("employee"), "wrong-password-here").status_code == 401
        assert emailed_codes.sent == []

    def test_wrong_password_never_sends_a_code(self, api, world, emailed_codes):
        enable_two_step(api, world, emailed_codes)
        sent = len(emailed_codes.sent)
        r = post_login(api, world.email("employee"), "wrong-password-here")
        assert r.status_code == 401 and "mfa_required" not in r.json() and len(emailed_codes.sent) == sent

    def test_mail_failure_is_reported_and_a_new_code_can_be_requested_at_once(self, api, world, emailed_codes, monkeypatch):
        from opsdesk.services import mailer
        email = world.email("employee")
        enable_two_step(api, world, emailed_codes)
        working = mailer.send_mail

        def broken(*a, **kw):
            raise OSError("mail server down")
        monkeypatch.setattr(mailer, "send_mail", broken)
        r = post_login(api, email, World.PASSWORD)
        assert r.status_code == 503 and "couldn't send the code" in r.json()["detail"]
        monkeypatch.setattr(mailer, "send_mail", working)
        assert login_with_code(api, world, emailed_codes)

    def test_wrong_code_is_refused_and_turning_on_twice_is_blocked(self, api, world, emailed_codes):
        h = login(api, world, "team_lead")
        api.post("/api/auth/mfa/", headers=h, json={})
        assert api.post("/api/auth/mfa/", headers=h, json={"code": wrong(emailed_codes.last(world.email("team_lead")))}).status_code == 400
        assert api.get("/api/auth/me/", headers=h).json()["mfa_enabled"] is False
        assert api.post("/api/auth/mfa/", headers={}, json={}).status_code == 401
        h2 = enable_two_step(api, world, emailed_codes, "manager")
        assert api.post("/api/auth/mfa/", headers=h2, json={}).status_code == 409

    def test_a_code_for_one_purpose_or_person_does_not_work_for_another(self, api, world, emailed_codes):
        he = enable_two_step(api, world, emailed_codes, "employee")
        hm = login(api, world, "manager")
        api.post("/api/auth/mfa/", headers=hm, json={})
        manager_code = emailed_codes.last(world.email("manager"))
        assert api.post("/api/auth/mfa/disable/", headers=he, json={}).status_code == 200
        employee_off_code = emailed_codes.last(world.email("employee"))
        if manager_code != employee_off_code:  # equal only by a one-in-a-million draw
            assert api.post("/api/auth/mfa/disable/", headers=he, json={"code": manager_code}).status_code == 400
        post_login(api, world.email("employee"), World.PASSWORD)
        login_code = emailed_codes.last(world.email("employee"))
        if login_code != employee_off_code:
            assert post_login(api, world.email("employee"), World.PASSWORD, otp=employee_off_code).status_code == 401

    def test_turning_it_off_needs_the_emailed_code_and_is_audited(self, api, world, emailed_codes):
        email = world.email("employee")
        h = enable_two_step(api, world, emailed_codes)
        r = api.post("/api/auth/mfa/disable/", headers=h, json={})
        assert r.status_code == 200 and r.json()["mfa_enabled"] is True and r.json()["sent_to"]
        code = emailed_codes.last(email)
        assert api.post("/api/auth/mfa/disable/", headers=h, json={"code": wrong(code)}).status_code == 400
        assert api.get("/api/auth/me/", headers=h).json()["mfa_enabled"] is True
        assert api.post("/api/auth/mfa/disable/", headers=h, json={"code": code}).json()["mfa_enabled"] is False
        assert api.post("/api/auth/mfa/disable/", headers=h, json={}).status_code == 409
        assert post_login(api, email, World.PASSWORD).status_code == 200
        with tenant_session(world.tenant_id) as s:
            actions = set(s.scalars(select(AuditLog.action).where(AuditLog.action.like("auth.mfa_%"))))
            assert actions == {"auth.mfa_enabled", "auth.mfa_disabled"}
            assert all(code not in str(c) for c in s.scalars(select(AuditLog.changes)))

    def test_accounts_set_up_with_an_authenticator_app_now_get_emailed_codes(self, api, world, emailed_codes):
        with tenant_session(world.tenant_id) as s:
            u = s.get(User, world.users["employee"])
            u.mfa_enabled, u.mfa_secret = True, "JBSWY3DPEHPK3PXP"
        assert login_with_code(api, world, emailed_codes)

    def test_too_many_wrong_codes_drop_the_code(self, api, world, emailed_codes):
        email = world.email("employee")
        enable_two_step(api, world, emailed_codes)
        post_login(api, email, World.PASSWORD)
        code = emailed_codes.last(email)
        for _ in range(5):
            assert post_login(api, email, World.PASSWORD, otp=wrong(code)).status_code == 401
        assert post_login(api, email, World.PASSWORD, otp=code).status_code == 401


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
