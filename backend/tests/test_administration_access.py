"""Administration (users, teams, SLA, configuration, channels, audit log) is open to the super admin only."""
import pytest

from tests.conftest import make_world

ENDPOINTS = [
    "/api/admin/users/", "/api/admin/user-options/", "/api/admin/teams/", "/api/admin/sla-policies/",
    "/api/admin/business-hours/", "/api/admin/config/", "/api/admin/config/workflows/",
    "/api/admin/audit-logs/", "/api/admin/audit-logs/actions/", "/api/channels/", "/api/admin/overview/",
]


@pytest.fixture
def aworld():
    return make_world(roles=("super_admin", "manager", "team_lead", "employee", "customer"))


@pytest.mark.parametrize("path", ENDPOINTS)
def test_only_the_super_admin_gets_in(api, aworld, auth, path):
    assert api.get(path, headers=auth("super_admin", aworld)).status_code == 200, path
    for role in ("manager", "team_lead", "employee", "customer"):
        assert api.get(path, headers=auth(role, aworld)).status_code == 403, f"{role} {path}"
    assert api.get(path).status_code == 401


def test_writes_are_refused_for_a_manager(api, aworld, auth):
    h = auth("manager", aworld)
    assert api.post("/api/admin/teams/", headers=h, json={"name": "X"}).status_code == 403
    assert api.post("/api/admin/config/categories/", headers=h, json={"name": "X"}).status_code == 403
    uid = aworld.users["employee"]
    assert api.post(f"/api/admin/users/{uid}/deactivate/", headers=h).status_code == 403


def test_everyday_screens_still_work_for_a_manager(api, aworld, auth):
    h = auth("manager", aworld)
    assert api.get("/api/meta/", headers=h).status_code == 200
    assert api.get("/api/tickets/", headers=h).status_code == 200
    assert api.get("/api/reports/overview/", headers=h).status_code == 200


def test_overview_summarises_the_five_administration_pages(api, aworld, auth):
    body = api.get("/api/admin/overview/", headers=auth("super_admin", aworld)).json()
    assert set(body) == {"users", "teams", "sla", "config", "audit", "generated_at"}
    users = body["users"]
    assert users["total"] == users["active"] + users["deactivated"]
    assert [r["role"] for r in users["by_role"]] == ["super_admin", "manager", "team_lead", "employee", "customer"]
    assert sum(r["count"] for r in users["by_role"]) == users["active"]
    assert body["teams"]["total"] >= body["teams"]["without_members"]
    assert len(body["audit"]["series"]) == 14
    assert all("total" in r and "label" in r for r in body["config"])
    assert "password" not in str(body).lower()


def test_super_admin_adds_users_from_the_console(api, aworld, auth):
    h = auth("super_admin", aworld)
    team = api.post("/api/admin/teams/", headers=h, json={"name": "Intake"}).json()["id"]
    body = {"email": "New.Person@Example.com", "first_name": "New", "last_name": "Person", "role": "employee",
            "team_id": team, "password": "Long-Enough-Pw-2026"}
    r = api.post("/api/admin/users/", headers=h, json=body)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["email"] == "new.person@example.com" and created["role"] == "employee" and created["is_active"]
    assert [t["id"] for t in created["teams"]] == [team]
    # the new account can sign in with the password it was given
    login = api.post("/api/auth/login/", json={"email": "new.person@example.com", "password": body["password"]})
    assert login.status_code == 200, login.text
    assert "password" not in str(api.get("/api/admin/audit-logs/", headers=h).json()).lower().replace("password_set", "")
    # a second account with the same email, in any case, is refused
    assert api.post("/api/admin/users/", headers=h, json={**body, "email": "new.person@example.com"}).status_code == 409


def test_adding_users_is_checked_and_super_admin_only(api, aworld, auth):
    h = auth("super_admin", aworld)
    team = api.post("/api/admin/teams/", headers=h, json={"name": "Desk"}).json()["id"]
    ok = {"email": "x.y@example.com", "first_name": "X", "role": "employee", "team_id": team, "password": "Long-Enough-Pw-2026"}
    no_team = {k: v for k, v in ok.items() if k != "team_id"}
    assert api.post("/api/admin/users/", headers=h, json=no_team).status_code == 400  # an employee needs a team
    assert api.post("/api/admin/users/", headers=h, json={**ok, "team_id": 999999}).status_code == 400
    assert api.post("/api/admin/users/", headers=h, json={**ok, "role": "manager"}).status_code == 400  # only employees take a team
    assert api.post("/api/admin/users/", headers=h, json={**ok, "role": "super_admin"}).status_code == 403
    assert api.post("/api/admin/users/", headers=h, json={**ok, "role": "wizard"}).status_code == 400
    assert api.post("/api/admin/users/", headers=h, json={**ok, "email": "not-an-email"}).status_code == 400
    assert api.post("/api/admin/users/", headers=h, json={**ok, "password": "short"}).status_code == 400
    assert api.post("/api/admin/users/", headers=h, json={**no_team, "role": "customer", "account_id": 999999}).status_code == 400
    assert api.post("/api/admin/users/", headers=h, json={**no_team, "role": "customer"}).status_code == 400  # a company is needed
    assert api.post("/api/admin/users/", headers=h, json={**ok, "account_id": 1}).status_code == 400  # staff have none
    for role in ("manager", "team_lead", "employee", "customer"):
        assert api.post("/api/admin/users/", headers=auth(role, aworld), json=ok).status_code == 403, role
    assert api.post("/api/admin/users/", json=ok).status_code == 401


def test_a_team_names_its_team_lead_and_manager(api, aworld, auth):
    h = auth("super_admin", aworld)
    team = api.post("/api/admin/teams/", headers=h, json={"name": "Platform"}).json()
    lead, mgr, emp = aworld.users["team_lead"], aworld.users["manager"], aworld.users["employee"]
    opts = api.get("/api/admin/user-options/", headers=h).json()
    assert [u["id"] for u in opts["team_leads"]] == [lead] and [u["id"] for u in opts["team_managers"]] == [mgr]
    r = api.patch(f"/api/admin/teams/{team['id']}/", headers={**h, "If-Match": f'"{team["version"]}"'},
                  json={"team_lead_id": lead, "manager_id": mgr})
    assert r.status_code == 200, r.text
    assert r.json()["team_lead"]["id"] == lead and r.json()["manager"]["id"] == mgr
    listed = next(t for t in api.get("/api/admin/teams/", headers=h).json()["results"] if t["id"] == team["id"])
    assert listed["team_lead"]["id"] == lead and listed["manager"]["id"] == mgr
    version = r.json()["version"]
    hh = {**h, "If-Match": f'"{version}"'}
    # each slot takes only a user holding that role
    assert api.patch(f"/api/admin/teams/{team['id']}/", headers=hh, json={"team_lead_id": emp}).status_code == 400
    assert api.patch(f"/api/admin/teams/{team['id']}/", headers=hh, json={"team_lead_id": mgr}).status_code == 400
    assert api.patch(f"/api/admin/teams/{team['id']}/", headers=hh, json={"manager_id": lead}).status_code == 400
    assert api.patch(f"/api/admin/teams/{team['id']}/", headers=hh, json={"manager_id": 999999}).status_code == 400
    cleared = api.patch(f"/api/admin/teams/{team['id']}/", headers=hh, json={"team_lead_id": None, "manager_id": None})
    assert cleared.status_code == 200 and cleared.json()["team_lead"] is None and cleared.json()["manager"] is None
    # a new team can name them straight away
    made = api.post("/api/admin/teams/", headers=h, json={"name": "Data", "team_lead_id": lead, "manager_id": mgr})
    assert made.status_code == 201 and made.json()["team_lead"]["id"] == lead
