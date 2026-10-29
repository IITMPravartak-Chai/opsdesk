"""Rules for the administration APIs (U7-A2 Teams and Users, U7-A3 SLA configuration). Pure Python (no web framework, no
FastAPI) so every rule can be tested on its own; the routers apply them inside the caller's tenant context.

Who may do what (the server enforces all of it; the UI only mirrors it):
- Users: managers and team leads may list and view. Managers (and super admins, who have the same access for now) may edit every profile field, change roles and
  deactivate or reactivate. Team leads may edit only non-access profile fields (name, job title, phone, time zone,
  department, availability, skills) and only of employees and customers. Team leads never change a role, the
  active flag, the account, the reporting line, or anything about a manager or another team lead.
  Nobody changes their own role or deactivates themselves, and the last active manager cannot be demoted or
  deactivated. Email, password, two-step sign-in, staff and superuser flags are not writable through these APIs at all.
  Creating users is not offered: there is no secure invitation or password-setup flow (owner decision D-4 is open).
- Teams: managers and team leads. Members must be staff (manager, team lead or employee) of the caller's tenant, and
  anyone being added must also be active, because automatic assignment picks from team members.
- SLA policies and business hours: managers only (provisional owner decision), for reading and writing.

Concurrency: these models have no version column, so a resource's `version` is a hash of its editable state. Writes
must send it back in If-Match (428 when missing, 412 when stale); the routers compare it under a row lock.
"""
import hashlib
import json
import re
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROLES = ("super_admin", "manager", "team_lead", "employee", "customer")  # mirrors User.ROLES in db/models/core.py
STAFF_ROLES = ("super_admin", "manager", "team_lead", "employee")
TOP_ROLES = ("super_admin", "manager")
PRIVILEGED_ROLES = ("super_admin", "manager", "team_lead")

USER_READ_ROLES = PRIVILEGED_ROLES
TEAM_ROLES = PRIVILEGED_ROLES
SLA_ROLES = TOP_ROLES

SUPER_ADMIN = "super_admin"
SUPER_ADMIN_LOCKED = "The super admin account can't be changed here."

PROFILE_FIELDS = ("first_name", "last_name", "job_title", "phone", "timezone", "department_id", "is_available", "skills")
MANAGER_ONLY_FIELDS = ("account_id", "manager_id", "role")
TEAM_LEAD_TARGET_ROLES = ("employee", "customer")

ASSIGNMENT_STRATEGIES = ("manual", "round_robin", "least_loaded")  # mirrors Team.STRATEGIES in db/models/core.py

MAX_TARGET_MINUTES = 527_040  # 366 days
MAX_HOLIDAYS = 400
MAX_RANGES_PER_DAY = 6


class RuleError(Exception):
    """A rule refused the request. `status` is the HTTP status; `detail` is safe to show to the caller."""

    def __init__(self, status, detail, extra=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra or {}


def invalid(field):
    return RuleError(400, f"Invalid {field}.")


# ---------------------------------------------------------------- concurrency
def version_of(state):
    """Stable short hash of a resource's editable state (dicts are key-sorted; lists keep their order)."""
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def check_if_match(header, current, noun):
    """Compare an If-Match header with the current version. Accepts the bare value, a quoted one or a weak ETag."""
    if not header or not header.strip():
        raise RuleError(428, f"Reload the {noun} and try again: this change needs the version you last loaded.")
    sent = [v.strip().removeprefix("W/").strip('"') for v in header.split(",")]
    if "*" in sent or current in sent:
        return
    raise RuleError(412, f"This {noun} was changed by someone else. Reload it and try again.")


# ---------------------------------------------------------------- users
def can_read_users(role):
    return role in USER_READ_ROLES


def user_permissions(actor_role, actor_id, target_role, target_id):
    """What the caller may do to one user. The same function decides the API checks and the UI hints."""
    is_self = actor_id == target_id
    if target_role == SUPER_ADMIN:  # nobody edits the super admin through these APIs, the super admin included
        return {"editable_fields": [], "can_change_role": False, "can_deactivate": False}
    if actor_role in TOP_ROLES:
        fields = PROFILE_FIELDS + MANAGER_ONLY_FIELDS
        return {"editable_fields": list(fields if not is_self else [f for f in fields if f != "role"]),
                "can_change_role": not is_self, "can_deactivate": not is_self}
    if actor_role == "team_lead" and target_role in TEAM_LEAD_TARGET_ROLES:
        return {"editable_fields": list(PROFILE_FIELDS), "can_change_role": False, "can_deactivate": False}
    return {"editable_fields": [], "can_change_role": False, "can_deactivate": False}


def check_user_update(actor_role, actor_id, target_role, target_id, fields):
    """Refuse a PATCH that touches anything the caller may not change (403) or their own role (409)."""
    if actor_role not in USER_READ_ROLES:
        raise RuleError(403, "Only managers and team leads can manage users.")
    check_not_super_admin(target_role)
    if actor_role == "team_lead" and target_role not in TEAM_LEAD_TARGET_ROLES:
        raise RuleError(403, "Team leads can change details only for employees and customers.")
    if actor_role == "team_lead" and "role" in fields:
        raise RuleError(403, "Only managers can change roles.")
    allowed = set(user_permissions(actor_role, actor_id, target_role, target_id)["editable_fields"])
    if "role" in fields and actor_id == target_id:
        raise RuleError(409, "You can't change your own role.")
    refused = sorted(set(fields) - allowed)
    if refused:
        raise RuleError(403, "You can't change " + ", ".join(f.removesuffix("_id") for f in refused) + " for this user.")


def check_not_super_admin(target_role):
    """The single super admin account is not editable, deactivatable or recoverable by any other role."""
    if target_role == SUPER_ADMIN:
        raise RuleError(403, SUPER_ADMIN_LOCKED)


def check_status_change(actor_role, actor_id, target_id, deactivate):
    if actor_role not in TOP_ROLES:
        raise RuleError(403, "Only managers can deactivate or reactivate users.")
    if deactivate and actor_id == target_id:
        raise RuleError(409, "You can't deactivate your own account.")


def staff_flag(role, current):
    """is_staff after a role change: on for manager and team lead (staff access follows these roles), off
    for every other role so a demoted user keeps no staff flag."""
    return True if role in PRIVILEGED_ROLES else False if role in ROLES else current


_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{2,30}$")


def valid_timezone(name):
    if not isinstance(name, str) or not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def clean_profile(values):
    """Validate and normalise the plain profile fields of a PATCH (ids are checked by the router). Returns a new dict."""
    out = {}
    for f in ("first_name", "last_name"):
        if f in values:
            v = values[f]
            if not isinstance(v, str) or len(v.strip()) > 150:
                raise invalid(f)
            out[f] = v.strip()
    if "job_title" in values:
        v = values["job_title"]
        if not isinstance(v, str) or len(v.strip()) > 120:
            raise invalid("job_title")
        out["job_title"] = v.strip()
    if "phone" in values:
        v = values["phone"]
        if not isinstance(v, str) or (v.strip() and not _PHONE.match(v.strip())):
            raise invalid("phone")
        out["phone"] = v.strip()
    if "timezone" in values:
        v = values["timezone"]
        if not isinstance(v, str) or (v.strip() and not valid_timezone(v.strip())):
            raise invalid("timezone")
        out["timezone"] = v.strip()
    if "is_available" in values:
        if not isinstance(values["is_available"], bool):
            raise invalid("is_available")
        out["is_available"] = values["is_available"]
    if "skills" in values:
        v = values["skills"]
        if not isinstance(v, list) or len(v) > 50 or any(not isinstance(s, str) or not s.strip() or len(s.strip()) > 60 for s in v):
            raise invalid("skills")
        seen, skills = set(), []
        for s in (s.strip() for s in v):
            if s.lower() not in seen:
                seen.add(s.lower())
                skills.append(s)
        out["skills"] = skills
    if "role" in values and values["role"] == SUPER_ADMIN:
        raise RuleError(403, "The super admin role can't be assigned here.")
    if "role" in values and values["role"] not in ROLES:
        raise invalid("role")
    if "role" in values:
        out["role"] = values["role"]
    return out


# Field changes recorded in the audit log as "changed", without values (personal contact data).
VALUE_HIDDEN_FIELDS = ("phone",)


def audit_changes(before, after):
    """{field: [old, new]} for fields that changed; personal contact fields are recorded without their values."""
    out = {}
    for k in after:
        if before.get(k) != after[k]:
            out[k] = "changed" if k in VALUE_HIDDEN_FIELDS else [before.get(k), after[k]]
    return out


# ---------------------------------------------------------------- teams
def can_manage_teams(role):
    return role in TEAM_ROLES


def normalize_name(name):
    return " ".join((name or "").split()).casefold()


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_team(values, creating):
    out = {}
    if "name" in values or creating:
        v = values.get("name")
        if not isinstance(v, str) or not v.strip() or len(" ".join(v.split())) > 120:
            raise invalid("name")
        out["name"] = " ".join(v.split())
    if "email" in values:
        v = values["email"]
        if v is None:
            v = ""
        if not isinstance(v, str) or len(v) > 254 or (v.strip() and not _EMAIL.match(v.strip())):
            raise invalid("email")
        out["email"] = v.strip()
    if "assignment_strategy" in values:
        if values["assignment_strategy"] not in ASSIGNMENT_STRATEGIES:
            raise invalid("assignment_strategy")
        out["assignment_strategy"] = values["assignment_strategy"]
    return out


def clean_id_list(values, field, limit=500):
    if not isinstance(values, list) or len(values) > limit or any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in values):
        raise invalid(field)
    return sorted(set(values))


def member_eligibility(role, is_active):
    """(eligible for assignment, reason shown when not)."""
    if role not in STAFF_ROLES:
        return False, "Not a staff account, so never assigned work"
    if not is_active:
        return False, "Deactivated, so not assigned work"
    return True, None


def check_members(requested, current_ids, users):
    """`users` maps id -> (role, is_active) for ids found in the caller's tenant. Every member must be staff of the
    tenant; anyone newly added must also be active. Returns nothing; raises 400 Invalid user_ids."""
    for uid in requested:
        if uid not in users:
            raise invalid("user_ids")
        role, active = users[uid]
        if role not in STAFF_ROLES or (uid not in current_ids and not active):
            raise invalid("user_ids")


# ---------------------------------------------------------------- SLA
def can_manage_sla(role):
    return role in SLA_ROLES


_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _minutes(hhmm, end=False):
    h, m = int(hhmm[:2]), int(hhmm[3:])
    v = h * 60 + m
    return 24 * 60 if end and v == 0 else v  # an end of "00:00" means midnight, as in the SLA engine


def clean_schedule(schedule):
    """Normalise {"0": [["09:00","18:00"]], ...} (0 = Monday). Ranges are sorted and must not overlap; start < end."""
    if not isinstance(schedule, dict):
        raise invalid("schedule")
    out = {}
    for key, ranges in schedule.items():
        if key not in tuple("0123456") or not isinstance(ranges, list) or len(ranges) > MAX_RANGES_PER_DAY:
            raise invalid("schedule")
        spans = []
        for r in ranges:
            if (not isinstance(r, list) or len(r) != 2 or not all(isinstance(x, str) and _HHMM.match(x) for x in r)):
                raise invalid("schedule")
            a, b = _minutes(r[0]), _minutes(r[1], end=True)
            if a >= b:
                raise RuleError(400, "Each working period must end after it starts.")
            spans.append((a, b, r))
        spans.sort()
        for (a1, b1, _), (a2, _b2, _) in zip(spans, spans[1:]):
            if a2 < b1:
                raise RuleError(400, "Working periods on the same day must not overlap.")
        if spans:
            out[key] = [list(r) for _a, _b, r in spans]
    return out


def working_minutes_per_week(schedule):
    return sum(_minutes(b, end=True) - _minutes(a) for ranges in schedule.values() for a, b in ranges)


def clean_holidays(holidays):
    if not isinstance(holidays, list) or len(holidays) > MAX_HOLIDAYS:
        raise invalid("holidays")
    out = set()
    for h in holidays:
        if not isinstance(h, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", h):
            raise invalid("holidays")
        try:
            date.fromisoformat(h)
        except ValueError:
            raise invalid("holidays")
        out.add(h)
    return sorted(out)


def clean_business_hours(values, current):
    """Validate a business-hours PATCH merged over `current` ({name, timezone, is_24x7, schedule, holidays}).
    Returns the full new state. A calendar that is not 24x7 must have working time: the SLA engine treats an empty
    schedule as always open, but a schedule with no usable period makes due-date calculation fail."""
    new = {**current, **{k: v for k, v in values.items() if k in ("name", "timezone", "is_24x7", "schedule", "holidays")}}
    # The whole merged state is validated, so a stored calendar the engine could not use is reported, not kept.
    v = new.get("name")
    if not isinstance(v, str) or not v.strip() or len(" ".join(v.split())) > 120:
        raise invalid("name")
    new["name"] = " ".join(v.split())
    if not valid_timezone(new.get("timezone")):
        raise invalid("timezone")
    if not isinstance(new.get("is_24x7"), bool):
        raise invalid("is_24x7")
    new["schedule"] = clean_schedule(new.get("schedule") or {})
    new["holidays"] = clean_holidays(new.get("holidays") or [])
    if not new["is_24x7"] and working_minutes_per_week(new["schedule"]) == 0:
        raise RuleError(400, "Add at least one working period, or mark these hours as 24x7.")
    return new


def clean_at_risk(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise invalid("at_risk_percent")
    return value


def clean_targets(targets, priority_ids):
    """Full replacement list of {priority_id, first_response_minutes, resolution_minutes}."""
    if not isinstance(targets, list) or len(targets) > 50:
        raise invalid("targets")
    seen, out = set(), []
    for t in targets:
        if not isinstance(t, dict) or set(t) != {"priority_id", "first_response_minutes", "resolution_minutes"}:
            raise invalid("targets")
        pid, fr, res = t["priority_id"], t["first_response_minutes"], t["resolution_minutes"]
        if pid not in priority_ids or pid in seen:
            raise invalid("targets")
        for v in (fr, res):
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_TARGET_MINUTES:
                raise RuleError(400, f"Targets must be between 1 and {MAX_TARGET_MINUTES} minutes.")
        if fr > res:
            raise RuleError(400, "The first-response target can't be longer than the resolution target.")
        seen.add(pid)
        out.append({"priority_id": pid, "first_response_minutes": fr, "resolution_minutes": res})
    return sorted(out, key=lambda t: t["priority_id"])


def default_conflicts(policies):
    """Scopes where the SLA engine would pick a default policy arbitrarily. `policies` is a list of
    {id, is_default, ticket_type_ids}. resolve_policy takes the first default naming the ticket's type, else the first
    default naming no type, with no ordering; so two defaults naming the same type, or two type-less defaults, are
    ambiguous. Returns [{"ticket_type_id": id or None, "policy_ids": [...]}]."""
    by_scope = {}
    for p in policies:
        if not p["is_default"]:
            continue
        for scope in (p["ticket_type_ids"] or [None]):
            by_scope.setdefault(scope, []).append(p["id"])
    return [{"ticket_type_id": s, "policy_ids": sorted(ids)}
            for s, ids in sorted(by_scope.items(), key=lambda kv: (kv[0] is not None, kv[0] or 0)) if len(ids) > 1]


def new_default_conflicts(before, after, policy_id):
    """Conflicts in `after` that involve `policy_id` and were not already present in `before` (a write may not create
    or widen an ambiguity; existing ones are reported but do not block unrelated edits)."""
    old = {(c["ticket_type_id"], tuple(c["policy_ids"])) for c in default_conflicts(before)}
    return [c for c in default_conflicts(after)
            if policy_id in c["policy_ids"] and (c["ticket_type_id"], tuple(c["policy_ids"])) not in old]
