"""U7-A2/A3: the administration rules on their own (no database). The routers' use of them is covered against a real
database in tests/test_api_admin_people_sla.py."""
import pytest

from opsdesk.api import admin_rules as r


def status_of(fn, *a, **kw):
    with pytest.raises(r.RuleError) as e:
        fn(*a, **kw)
    return e.value.status, e.value.detail


# ---------------------------------------------------------------- versions and If-Match
def test_version_is_stable_and_key_order_independent():
    assert r.version_of({"a": 1, "b": [2, 3]}) == r.version_of({"b": [2, 3], "a": 1})
    assert r.version_of({"a": 1}) != r.version_of({"a": 2})
    assert len(r.version_of({})) == 32


@pytest.mark.parametrize("header", ["abc", '"abc"', 'W/"abc"', '"x", "abc"', "*"])
def test_if_match_accepts_current(header):
    r.check_if_match(header, "abc", "user")


def test_if_match_missing_is_428_and_stale_is_412():
    assert status_of(r.check_if_match, None, "abc", "user")[0] == 428
    assert status_of(r.check_if_match, "  ", "abc", "user")[0] == 428
    code, detail = status_of(r.check_if_match, '"old"', "abc", "team")
    assert code == 412 and "changed by someone else" in detail


# ---------------------------------------------------------------- user permissions
def test_admin_permissions_exclude_own_role_and_deactivation():
    other = r.user_permissions("manager", 1, "employee", 2)
    assert other["can_change_role"] and other["can_deactivate"] and "role" in other["editable_fields"]
    me = r.user_permissions("manager", 1, "manager", 1)
    assert not me["can_change_role"] and not me["can_deactivate"] and "role" not in me["editable_fields"]


@pytest.mark.parametrize("target", ["employee", "customer"])
def test_manager_edits_only_profile_fields_of_non_privileged_users(target):
    p = r.user_permissions("team_lead", 1, target, 2)
    assert set(p["editable_fields"]) == set(r.PROFILE_FIELDS)
    assert not p["can_change_role"] and not p["can_deactivate"]
    r.check_user_update("team_lead", 1, target, 2, {"job_title", "is_available", "skills"})


@pytest.mark.parametrize("target", ["manager", "team_lead"])
def test_manager_cannot_touch_admins_or_managers(target):
    assert r.user_permissions("team_lead", 1, target, 2)["editable_fields"] == []
    code, detail = status_of(r.check_user_update, "team_lead", 1, target, 2, {"job_title"})
    assert code == 403 and "employees and customers" in detail


@pytest.mark.parametrize("field", ["role", "account_id", "manager_id"])
def test_manager_cannot_change_access_fields(field):
    assert status_of(r.check_user_update, "team_lead", 1, "employee", 2, {field})[0] == 403


def test_manager_role_change_message():
    assert status_of(r.check_user_update, "team_lead", 1, "customer", 2, {"role"}) == (403, "Only managers can change roles.")


def test_nobody_changes_their_own_role():
    assert status_of(r.check_user_update, "manager", 1, "manager", 1, {"role"}) == (409, "You can't change your own role.")
    r.check_user_update("manager", 1, "manager", 1, {"job_title"})


@pytest.mark.parametrize("role", ["employee", "customer"])
def test_other_roles_cannot_manage_users(role):
    assert not r.can_read_users(role)
    assert status_of(r.check_user_update, role, 1, "customer", 2, {"job_title"})[0] == 403


def test_status_changes_are_admin_only_and_not_self():
    assert status_of(r.check_status_change, "team_lead", 1, 2, True)[0] == 403
    assert status_of(r.check_status_change, "manager", 1, 1, True) == (409, "You can't deactivate your own account.")
    r.check_status_change("manager", 1, 1, False)
    r.check_status_change("manager", 1, 2, True)


def test_staff_flag_follows_role():
    assert r.staff_flag("manager", False) is True and r.staff_flag("team_lead", False) is True
    assert r.staff_flag("employee", True) is False and r.staff_flag("customer", True) is False


def test_clean_profile_validates_and_normalises():
    out = r.clean_profile({"first_name": "  Asha ", "phone": "+91 98450 12345", "timezone": "Asia/Kolkata",
                           "is_available": False, "skills": ["Network", "network", " VPN "], "role": "employee"})
    assert out == {"first_name": "Asha", "phone": "+91 98450 12345", "timezone": "Asia/Kolkata", "is_available": False,
                   "skills": ["Network", "VPN"], "role": "employee"}
    assert r.clean_profile({"phone": "", "timezone": ""}) == {"phone": "", "timezone": ""}


@pytest.mark.parametrize("values,field", [
    ({"first_name": "x" * 151}, "first_name"), ({"phone": "call me"}, "phone"), ({"timezone": "Mars/Base"}, "timezone"),
    ({"is_available": "yes"}, "is_available"), ({"skills": "network"}, "skills"), ({"skills": [""]}, "skills"),
    ({"role": "superuser"}, "role"), ({"job_title": 5}, "job_title"),
])
def test_clean_profile_rejects(values, field):
    assert status_of(r.clean_profile, values) == (400, f"Invalid {field}.")


def test_audit_changes_hide_contact_values():
    assert r.audit_changes({"phone": "1", "role": "employee", "x": 1}, {"phone": "2", "role": "team_lead", "x": 1}) == \
        {"phone": "changed", "role": ["employee", "team_lead"]}


# ---------------------------------------------------------------- teams
def test_team_names_compare_case_and_space_insensitively():
    assert r.normalize_name("  Network   Ops ") == r.normalize_name("network ops")


def test_clean_team():
    assert r.clean_team({"name": " Network  Ops ", "email": "", "assignment_strategy": "round_robin"}, True) == \
        {"name": "Network Ops", "email": "", "assignment_strategy": "round_robin"}
    for bad in ({"name": " "}, {"name": "x" * 121}, {"email": "nope"}, {"assignment_strategy": "random"}):
        assert status_of(r.clean_team, {"name": "ok", **bad}, True)[0] == 400
    assert status_of(r.clean_team, {}, True) == (400, "Invalid name.")
    assert r.clean_team({}, False) == {}


def test_id_lists():
    assert r.clean_id_list([3, 1, 3], "member_ids") == [1, 3]
    for bad in ("1", [0], [True], ["2"], list(range(1, 502))):
        assert status_of(r.clean_id_list, bad, "member_ids") == (400, "Invalid member_ids.")


def test_member_eligibility():
    assert r.member_eligibility("employee", True) == (True, None)
    assert r.member_eligibility("customer", True)[0] is False
    assert r.member_eligibility("team_lead", False)[0] is False


def test_check_members():
    users = {1: ("employee", True), 2: ("customer", True), 3: ("employee", False)}
    r.check_members([1], set(), users)
    r.check_members([1, 3], {3}, users)  # an existing member who was deactivated may stay
    for requested, current in (([2], set()), ([2], {2}), ([3], set()), ([9], set())):
        assert status_of(r.check_members, requested, current, users) == (400, "Invalid user_ids.")


# ---------------------------------------------------------------- SLA
def test_only_admins_manage_sla():
    assert r.can_manage_sla("manager")
    for role in ("team_lead", "employee", "customer"):
        assert not r.can_manage_sla(role)


def test_schedule_is_sorted_and_midnight_end_allowed():
    s = r.clean_schedule({"0": [["13:00", "18:00"], ["09:00", "12:00"]], "5": [["22:00", "00:00"]], "6": []})
    assert s == {"0": [["09:00", "12:00"], ["13:00", "18:00"]], "5": [["22:00", "00:00"]]}
    assert r.working_minutes_per_week(s) == 3 * 60 + 5 * 60 + 2 * 60


@pytest.mark.parametrize("schedule", [
    [], {"7": [["09:00", "10:00"]]}, {"0": [["9:00", "10:00"]]}, {"0": [["09:00"]]}, {"0": [["24:00", "10:00"]]},
    {"0": "09:00-18:00"}, {"0": [["09:00", "10:00"]] * 7},
])
def test_schedule_shape_rejected(schedule):
    assert status_of(r.clean_schedule, schedule)[0] == 400


def test_schedule_order_and_overlap_rejected():
    assert "end after it starts" in status_of(r.clean_schedule, {"0": [["18:00", "09:00"]]})[1]
    assert "end after it starts" in status_of(r.clean_schedule, {"0": [["09:00", "09:00"]]})[1]
    assert "overlap" in status_of(r.clean_schedule, {"1": [["09:00", "13:00"], ["12:00", "18:00"]]})[1]


BASE = {"name": "IST weekdays", "timezone": "Asia/Kolkata", "is_24x7": False,
        "schedule": {"0": [["09:00", "18:00"]]}, "holidays": []}


def test_business_hours_need_working_time_unless_24x7():
    assert r.clean_business_hours({}, BASE)["schedule"] == {"0": [["09:00", "18:00"]]}
    code, detail = status_of(r.clean_business_hours, {"schedule": {}}, BASE)
    assert code == 400 and "24x7" in detail
    assert status_of(r.clean_business_hours, {"schedule": {"0": []}}, BASE)[0] == 400
    assert r.clean_business_hours({"schedule": {}, "is_24x7": True}, BASE)["is_24x7"] is True


def test_business_hours_validate_stored_state_too():
    assert status_of(r.clean_business_hours, {}, {**BASE, "timezone": "Nowhere/City"}) == (400, "Invalid timezone.")
    assert status_of(r.clean_business_hours, {}, {**BASE, "schedule": {"0": [["9", "10"]]}}) == (400, "Invalid schedule.")


def test_holidays():
    assert r.clean_holidays(["2026-11-08", "2026-01-26", "2026-11-08"]) == ["2026-01-26", "2026-11-08"]
    for bad in ("2026-11-08", ["2026-02-30"], ["08/11/2026"], ["2026-01-01"] * 401):
        assert status_of(r.clean_holidays, bad)[0] == 400


def test_at_risk_percent():
    assert r.clean_at_risk(60) == 60
    for bad in (0, 101, 50.5, True, "60"):
        assert status_of(r.clean_at_risk, bad) == (400, "Invalid at_risk_percent.")


def test_targets():
    out = r.clean_targets([{"priority_id": 2, "first_response_minutes": 60, "resolution_minutes": 480},
                           {"priority_id": 1, "first_response_minutes": 15, "resolution_minutes": 240}], {1, 2})
    assert [t["priority_id"] for t in out] == [1, 2]
    one = {"priority_id": 1, "first_response_minutes": 15, "resolution_minutes": 240}
    assert status_of(r.clean_targets, [one, one], {1})[0] == 400
    assert status_of(r.clean_targets, [{**one, "priority_id": 9}], {1}) == (400, "Invalid targets.")
    assert "can't be longer" in status_of(r.clean_targets, [{**one, "first_response_minutes": 300}], {1})[1]
    assert "between 1 and" in status_of(r.clean_targets, [{**one, "resolution_minutes": 0}], {1})[1]
    assert "between 1 and" in status_of(r.clean_targets, [{**one, "resolution_minutes": r.MAX_TARGET_MINUTES + 1}], {1})[1]
    assert status_of(r.clean_targets, [{**one, "extra": 1}], {1}) == (400, "Invalid targets.")


def test_default_conflicts_follow_resolve_policy():
    pol = [{"id": 1, "is_default": True, "ticket_type_ids": []},
           {"id": 2, "is_default": True, "ticket_type_ids": []},
           {"id": 3, "is_default": True, "ticket_type_ids": [7]},
           {"id": 4, "is_default": False, "ticket_type_ids": [7]},
           {"id": 5, "is_default": True, "ticket_type_ids": [8]}]
    # two type-less defaults are ambiguous; a typed default beside a type-less one is not (the typed one wins)
    assert r.default_conflicts(pol) == [{"ticket_type_id": None, "policy_ids": [1, 2]}]
    pol.append({"id": 6, "is_default": True, "ticket_type_ids": [7, 8]})
    assert r.default_conflicts(pol)[1:] == [{"ticket_type_id": 7, "policy_ids": [3, 6]}, {"ticket_type_id": 8, "policy_ids": [5, 6]}]


def test_new_conflicts_only_block_the_policy_being_changed():
    before = [{"id": 1, "is_default": True, "ticket_type_ids": []}, {"id": 2, "is_default": True, "ticket_type_ids": []},
              {"id": 3, "is_default": False, "ticket_type_ids": []}]
    assert r.new_default_conflicts(before, before, 1) == []  # existing ambiguity: reported elsewhere, not a new one
    after = [*before[:2], {"id": 3, "is_default": True, "ticket_type_ids": []}]
    assert r.new_default_conflicts(before, after, 3) == [{"ticket_type_id": None, "policy_ids": [1, 2, 3]}]
