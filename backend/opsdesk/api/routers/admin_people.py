"""Teams and Users administration API.

GET   /api/admin/users/                       users of the caller's tenant (manager, team lead)
GET   /api/admin/user-options/                departments, accounts, managers and staff of the tenant (for the forms)
GET   /api/admin/users/{id}/                  one user, with `version` (also sent as ETag) and the caller's permissions
PATCH /api/admin/users/{id}/                  profile and, for managers, role, account and reporting line (If-Match)
POST  /api/admin/users/{id}/deactivate/       managers only (If-Match)
POST  /api/admin/users/{id}/reactivate/       managers only (If-Match)
POST  /api/admin/users/{id}/password/         managers only: set a new password (audited)
POST  /api/admin/users/{id}/mfa/disable/      managers only: turn off a user's two-step sign-in (audited)
GET   /api/admin/teams/                       teams (manager, team lead)
POST  /api/admin/teams/                       create a team
GET   /api/admin/teams/{id}/                  one team with members, their assignment eligibility and what uses the team
PATCH /api/admin/teams/{id}/                  name, email, assignment strategy, accounts (If-Match)
PUT   /api/admin/teams/{id}/members/          replace the member list (If-Match)
DELETE /api/admin/teams/{id}/                 only a team nothing refers to (If-Match); otherwise 409 with the counts

User creation is a platform operation (``opsdesk create-user``): there is no secure invitation or password-setup flow
in the web console. There is no user deletion (deactivate instead: a user's audit history must stay attributable,
and the append-only audit table refuses the update a delete would cause). The permission rules live in admin_rules.py.

The tenant comes from the signed token only. core_user has row-level security enabled but not forced, so every user
query also filters tenant_id explicitly; ids in a path from another tenant give 404, ids in a body give 400. Every
successful write records one admin.* audit event in the same transaction; a refused write records none.
"""
from __future__ import annotations

import re

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from ...db.models import (Account, Category, ConfigurationItem, Department, Service, ServiceCatalogItem, Team,
                          TeamAccount, TeamMember, Ticket, User, State)
from ...security import passwords
from ...services.audit import audit
from .. import admin_rules as rules
from .. import audit_view
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_current_user, require_administration

router = APIRouter(prefix="/api/admin", tags=["admin-people"], dependencies=[Depends(require_administration)])

DONE = ("resolved", "closed", "cancelled")


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: Any = None
    last_name: Any = None
    job_title: Any = None
    phone: Any = None
    timezone: Any = None
    department_id: Any = None
    account_id: Any = None
    manager_id: Any = None
    is_available: Any = None
    skills: Any = None
    role: Any = None


class TeamWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    email: Any = None
    assignment_strategy: Any = None
    member_ids: Any = None
    account_ids: Any = None
    team_lead_id: Any = None
    manager_id: Any = None


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    email: Any = None
    assignment_strategy: Any = None
    account_ids: Any = None
    team_lead_id: Any = None
    manager_id: Any = None


class MembersPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: Any


class PasswordSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    first_name: str = ""
    last_name: str = ""
    role: str = "employee"
    account_id: Any = None
    team_id: Any = None
    password: str


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _respond(body, version=None, code=200):
    headers = {"ETag": f'"{version}"'} if version else None
    return JSONResponse(status_code=code, content=body, headers=headers)


async def _run(user, fn, *, commit=True):
    """Run work in the caller's tenant transaction; a RuleError raised inside it rolls the transaction back and
    becomes a {"detail": ...} response with its status."""
    try:
        return await run_in_tenant(user, fn, commit=commit)
    except rules.RuleError as e:
        return JSONResponse(status_code=e.status, content={"detail": e.detail, **e.extra})


def _actor(s: Session, user: AuthenticatedUser) -> User:
    a = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id,
                                     User.is_active.is_(True))).first()
    if a is None:
        raise rules.RuleError(401, "User is inactive or not found.")
    return a


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _ref(obj, label="name"):
    return {"id": obj.id, "name": getattr(obj, label)} if obj is not None else None


# ---------------------------------------------------------------- users
def _users(tid):
    # Superusers are platform operators: never listed, edited or counted here.
    return [User.tenant_id == tid, User.is_superuser.is_(False)]


def _user_state(u):
    return {"first_name": u.first_name, "last_name": u.last_name, "job_title": u.job_title, "phone": u.phone,
            "timezone": u.timezone, "department_id": u.department_id, "account_id": u.account_id,
            "manager_id": u.manager_id, "is_available": u.is_available, "skills": list(u.skills or []),
            "role": u.role, "is_active": u.is_active}


def _user_row(u):
    return {"id": u.id, "email": u.email, "name": u.display_name, "first_name": u.first_name, "last_name": u.last_name,
            "role": u.role, "is_active": u.is_active, "is_available": u.is_available, "job_title": u.job_title,
            "department": _ref(u.department), "account": _ref(u.account),
            "manager": {"id": u.manager.id, "name": u.manager.display_name} if u.manager_id else None,
            "teams": [{"id": t.id, "name": t.name} for t in u.teams],
            "mfa_enabled": u.mfa_enabled, "last_login": _iso(u.last_login), "date_joined": _iso(u.date_joined)}


def _user_detail(s, u, user):
    body = _user_row(u)
    open_assigned = s.scalar(select(func.count()).select_from(Ticket).join(State, State.id == Ticket.state_id).where(
        Ticket.tenant_id == u.tenant_id, Ticket.assignee_id == u.id, State.category.notin_(DONE)))
    team_lead_count = s.scalar(select(func.count()).select_from(User).where(
        *_users(u.tenant_id), User.manager_id == u.id, User.role == "team_lead", User.is_active.is_(True))) if u.role == "manager" else None
    body.update({"team_lead_count": team_lead_count, "phone": u.phone, "timezone": u.timezone, "skills": list(u.skills or []),
                 "open_assigned_count": open_assigned,
                 "permissions": rules.user_permissions(user.role, user.user_id, u.role, u.id)})
    version = rules.version_of(_user_state(u))
    body["version"] = version
    return body, version


def _load_user(s, tid, pk, lock=False):
    stmt = select(User).where(*_users(tid), User.id == pk)
    if lock:
        stmt = stmt.with_for_update()  # no joined loads: FOR UPDATE cannot lock the nullable side of an outer join
    u = s.scalars(stmt).first()
    if u is None:
        raise rules.RuleError(404, "Not found.")
    return u


def _lock_active_admins(s, tid):
    """Lock every active manager row, in id order, before any change that could remove one. Taking these locks
    first (and always in the same order) serialises concurrent demotions and deactivations without deadlocks."""
    return list(s.scalars(select(User.id).where(*_users(tid), User.role.in_(rules.TOP_ROLES), User.is_active.is_(True))
                          .order_by(User.id).with_for_update()))


def _check_last_admin(admin_ids, target):
    if target.id in admin_ids and len(admin_ids) <= 1:
        raise rules.RuleError(409, "This would leave the workspace without an active manager.")


def _require(cond, detail):
    if not cond:
        raise rules.RuleError(403, detail)


def _validate_reporting_line(s: Session, target: User) -> None:
    """A user cannot be their own manager, the manager must be in the same tenant, and the reporting line must not
    loop (depth-bounded walk up the chain)."""
    if not target.manager_id:
        return
    if target.manager_id == target.id:
        raise ValueError("A user cannot be their own manager.")
    mgr = target.manager
    if mgr is None or mgr.tenant_id != target.tenant_id:
        raise ValueError("Manager must belong to the same tenant.")
    visited, curr, depth = {target.id}, mgr, 0
    while curr is not None and depth < 500:
        if curr.id in visited:
            raise ValueError("Circular reporting line detected.")
        visited.add(curr.id)
        curr = curr.manager
        depth += 1


@router.get("/users/")
async def list_users(request: Request, q: Optional[str] = Query(None, max_length=100), role: Optional[str] = Query(None),
                     is_active: Optional[str] = Query(None), team: Optional[str] = Query(None),
                     ordering: Optional[str] = Query(None), page: Optional[str] = Query(None),
                     page_size: Optional[str] = Query(None), user: AuthenticatedUser = Depends(get_current_user)):
    orderings = {None: ("first_name", "last_name", "email"), "name": ("first_name", "last_name", "email"),
                 "email": ("email",), "role": ("role", "first_name"), "-last_login": ("-last_login", "email")}

    def _query(s):
        _require(rules.can_read_users(user.role), "Only managers and team leads can manage users.")
        if role is not None and role not in rules.ROLES:
            raise rules.invalid("role")
        if is_active not in (None, "true", "false"):
            raise rules.invalid("is_active")
        if ordering not in orderings:
            raise rules.invalid("ordering")
        try:
            page_no, size, offset = audit_view.parse_page(page, page_size)
        except audit_view.InvalidFilter as e:
            raise rules.invalid(e.field)
        conds = _users(user.tenant_id)
        if q and q.strip():
            like = f"%{q.strip()}%"
            conds.append(User.first_name.ilike(like) | User.last_name.ilike(like) | User.email.ilike(like))
        if role:
            conds.append(User.role == role)
        if is_active is not None:
            conds.append(User.is_active.is_(is_active == "true"))
        if team is not None:
            if not team.isdigit() or not s.scalars(select(Team.id).where(Team.id == int(team),
                                                                         Team.tenant_id == user.tenant_id)).first():
                raise rules.invalid("team")
            conds.append(User.id.in_(select(TeamMember.user_id).where(TeamMember.team_id == int(team))))
        order = [getattr(User, f.lstrip("-")).desc() if f.startswith("-") else getattr(User, f) for f in orderings[ordering]]
        count = s.scalar(select(func.count()).select_from(User).where(*conds))
        rows = s.scalars(select(User).options(joinedload(User.department), joinedload(User.account), joinedload(User.manager),
                                              selectinload(User.teams)).where(*conds).order_by(*order, User.id)
                         .offset(offset).limit(size)).unique().all()
        results = [_user_row(u) for u in rows]
        raw = {"q": q, "role": role, "is_active": is_active, "team": team, "ordering": ordering}
        base = f"{request.url.scheme}://{request.url.netloc}{request.url.path}"
        nxt, prev = audit_view.page_links(base, raw, page_no, size, count)
        return _respond({"count": count, "next": nxt, "previous": prev, "page": page_no, "page_size": size,
                         "results": results})

    return await _run(user, _query, commit=False)


@router.post("/users/")
async def create_user(payload: UserCreate, request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """Create a user in the caller's workspace with an initial password. The super admin account itself is never
    created here (``opsdesk create-user --role super_admin`` only). The password must satisfy the password policy and is
    never recorded in the audit log."""
    def _create(s):
        _require(user.role in rules.TOP_ROLES, "Only the super admin and managers can add users.")
        actor = _actor(s, user)
        if payload.role not in rules.ROLES:
            raise rules.invalid("role")
        rules.check_not_super_admin(payload.role)
        email = payload.email.strip().lower()
        if len(email) > 254 or not _EMAIL.match(email):
            raise rules.invalid("email")
        first, last = payload.first_name.strip(), payload.last_name.strip()
        if len(first) > 150 or len(last) > 150:
            raise rules.invalid("name")
        team = None
        if payload.role == "employee":
            if payload.team_id in (None, ""):
                raise rules.RuleError(400, "Choose the employee's team.")
            if not _is_id(payload.team_id):
                raise rules.invalid("team")
            team = s.scalars(select(Team).where(Team.tenant_id == user.tenant_id, Team.id == payload.team_id)).first()
            if team is None:
                raise rules.invalid("team")
        elif payload.team_id not in (None, ""):
            raise rules.RuleError(400, "Only employees are added to a team when they are created.")
        account = None
        if payload.role != "customer" and payload.account_id not in (None, ""):
            raise rules.RuleError(400, "Only customers belong to a company.")
        if payload.role == "customer" and payload.account_id in (None, ""):
            raise rules.RuleError(400, "Choose the customer's company.")
        if payload.account_id not in (None, ""):
            if not _is_id(payload.account_id):
                raise rules.invalid("account")
            account = s.scalars(select(Account).where(Account.tenant_id == user.tenant_id, Account.id == int(payload.account_id),
                                                      Account.is_active.is_(True))).first()
            if account is None:
                raise rules.invalid("account")
        problems = passwords.validate_password(payload.password, email=email, first_name=first, last_name=last, username=email)
        if problems:
            return JSONResponse(status_code=400, content={"detail": " ".join(problems), "errors": problems})
        target = User(email=email, username=email, tenant_id=user.tenant_id, role=payload.role, first_name=first, last_name=last,
                      is_staff=rules.staff_flag(payload.role, False), password=passwords.make_password(payload.password),
                      account_id=account.id if account else None)
        try:
            with s.begin_nested():  # the email is unique across the whole site, including other workspaces
                s.add(target)
                s.flush()
        except IntegrityError:
            raise rules.RuleError(409, "A user with that email already exists.")
        changes = {"email": email, "role": payload.role}
        if team is not None:
            s.add(TeamMember(team_id=team.id, user_id=target.id))
            changes["team"] = team.name
        audit(s, user.tenant_id, "admin.user.create", target, actor, changes, request)
        s.flush()
        s.refresh(target)
        body, version = _user_detail(s, _load_user(s, user.tenant_id, target.id), user)
        return _respond(body, version, 201)

    return await _run(user, _create)


@router.get("/user-options/")
async def user_options(user: AuthenticatedUser = Depends(get_current_user)):
    """Choices for the edit forms, from the caller's tenant: departments, accounts, possible reporting managers (active
    internal users) and staff who can be team members."""
    def _query(s):
        _require(rules.can_read_users(user.role), "Only managers and team leads can manage users.")
        tid = user.tenant_id
        people = list(s.scalars(select(User).where(*_users(tid), User.is_active.is_(True), User.role != "customer")
                                .order_by(User.first_name, User.last_name, User.email).limit(1000)))
        body = {"departments": [_ref(d) for d in s.scalars(select(Department).where(
                    Department.tenant_id == tid, Department.is_active.is_(True)).order_by(Department.name))],
                "accounts": [_ref(a) for a in s.scalars(select(Account).where(
                    Account.tenant_id == tid, Account.is_active.is_(True)).order_by(Account.name))],
                "managers": [{"id": u.id, "name": u.display_name, "role": u.role} for u in people],
                "team_leads": [{"id": u.id, "name": u.display_name, "email": u.email} for u in people if u.role == "team_lead"],
                "team_managers": [{"id": u.id, "name": u.display_name, "email": u.email} for u in people if u.role == "manager"],
                "staff": [{"id": u.id, "name": u.display_name, "email": u.email, "role": u.role}
                          for u in people if u.role in rules.STAFF_ROLES]}
        return _respond(body)

    return await _run(user, _query, commit=False)


@router.get("/users/{id}/")
async def get_user(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        _require(rules.can_read_users(user.role), "Only managers and team leads can manage users.")
        body, version = _user_detail(s, _load_user(s, user.tenant_id, id), user)
        return _respond(body, version)

    return await _run(user, _query, commit=False)


def _is_id(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _resolve_user_refs(s, data, target, tid):
    """Turn department_id / account_id / manager_id into instances of the caller's tenant (400 otherwise). A manager
    (reporting line) must be an active internal user other than the target; cycles are refused separately."""
    sources = {"department": select(Department).where(Department.tenant_id == tid),
               "account": select(Account).where(Account.tenant_id == tid),
               "manager": select(User).where(*_users(tid), User.is_active.is_(True),
                                             User.role.in_(rules.STAFF_ROLES), User.id != target.id)}
    out = {}
    for name, stmt in sources.items():
        if f"{name}_id" not in data:
            continue
        pk = data[f"{name}_id"]
        if pk is None:
            out[name] = None
            continue
        model = {"department": Department, "account": Account, "manager": User}[name]
        obj = s.scalars(stmt.where(model.id == pk)).first() if _is_id(pk) else None
        if obj is None:
            raise rules.invalid(name)
        out[name] = obj
    return out


def _reverify(s, actor, user):
    """Re-read the caller after waiting for row locks: a concurrent change may have demoted or deactivated them."""
    s.refresh(actor, ["role", "is_active"])
    if not actor.is_active:
        raise rules.RuleError(401, "User is inactive or not found.")
    if actor.role != user.role:
        raise rules.RuleError(403, "Your role has changed. Reload and try again.")


@router.patch("/users/{id}/")
async def update_user(payload: UserPatch, id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                      user: AuthenticatedUser = Depends(get_current_user)):
    sent = payload.model_fields_set
    data = {k: getattr(payload, k) for k in sent}

    def _update(s):
        _require(rules.can_read_users(user.role), "Only managers and team leads can manage users.")
        actor = _actor(s, user)
        admin_ids = _lock_active_admins(s, user.tenant_id) if "role" in sent else []
        target = _load_user(s, user.tenant_id, id, lock=True)
        _reverify(s, actor, user)
        rules.check_user_update(user.role, user.user_id, target.role, target.id, sent)
        rules.check_if_match(if_match, rules.version_of(_user_state(target)), "user")
        clean = rules.clean_profile(data)
        refs = _resolve_user_refs(s, data, target, user.tenant_id)
        if "role" in clean and clean["role"] not in rules.TOP_ROLES and target.is_active:
            _check_last_admin(admin_ids, target)
        before = _user_state(target)
        for k, v in clean.items():
            setattr(target, k, v)
        for k, v in refs.items():
            setattr(target, k, v)
        if "role" in clean:
            target.is_staff = rules.staff_flag(clean["role"], target.is_staff)
        try:
            s.flush()
            if "manager" in refs:
                s.refresh(target)
                _validate_reporting_line(s, target)
        except ValueError:
            raise rules.invalid("manager")
        after = _user_state(target)
        changes = rules.audit_changes(before, {k: after[k] for k in before if k in {*clean, *(f + "_id" for f in refs)}})
        if changes:
            audit(s, user.tenant_id, "admin.user.update", target, actor, changes)
        s.flush()
        s.refresh(target)
        body, version = _user_detail(s, _load_user(s, user.tenant_id, id), user)
        return _respond(body, version)

    return await _run(user, _update)


async def _set_active(id, if_match, user, active):
    def _change(s):
        actor = _actor(s, user)
        admin_ids = _lock_active_admins(s, user.tenant_id)
        target = _load_user(s, user.tenant_id, id, lock=True)
        rules.check_not_super_admin(target.role)
        rules.check_status_change(user.role, user.user_id, id, deactivate=not active)
        _reverify(s, actor, user)
        rules.check_if_match(if_match, rules.version_of(_user_state(target)), "user")
        if target.is_active != active:
            if not active:
                _check_last_admin(admin_ids, target)
            changes = {"is_active": [target.is_active, active]}
            target.is_active = active
            if not active:
                if target.is_available:
                    changes["is_available"] = [True, False]
                target.is_available = False  # out of automatic assignment as well
            s.flush()
            audit(s, user.tenant_id, "admin.user.reactivate" if active else "admin.user.deactivate", target, actor, changes)
        s.flush()
        body, version = _user_detail(s, _load_user(s, user.tenant_id, id), user)
        return _respond(body, version)

    return await _run(user, _change)


@router.post("/users/{id}/deactivate/")
async def deactivate_user(id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                          user: AuthenticatedUser = Depends(get_current_user)):
    """Sign-in and API access stop on the next request (the role and active flag are re-read every time). Open tickets
    stay assigned; the response's open_assigned_count tells the caller how many to reassign."""
    return await _set_active(id, if_match, user, active=False)


@router.post("/users/{id}/reactivate/")
async def reactivate_user(id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                          user: AuthenticatedUser = Depends(get_current_user)):
    """Availability for automatic assignment stays off until someone turns it on."""
    return await _set_active(id, if_match, user, active=True)


@router.post("/users/{id}/password/")
async def set_user_password(payload: PasswordSet, request: Request, id: int = Path(..., ge=1),
                            user: AuthenticatedUser = Depends(get_current_user)):
    """Managers set a new password for a user of the workspace. The password must satisfy the password policy;
    the change is audited (the password itself is never recorded)."""
    def _set(s):
        _require(user.role in rules.TOP_ROLES, "Only managers can set passwords.")
        actor = _actor(s, user)
        target = _load_user(s, user.tenant_id, id, lock=True)
        rules.check_not_super_admin(target.role)  # recovery of that account is an operator task (opsdesk reset-password)
        problems = passwords.validate_password(payload.password, email=target.email, first_name=target.first_name,
                                               last_name=target.last_name, username=target.username)
        if problems:
            return JSONResponse(status_code=400, content={"detail": " ".join(problems), "errors": problems})
        target.password = passwords.make_password(payload.password)
        audit(s, user.tenant_id, "admin.user.password_set", target, actor, {}, request)
        return _respond({"ok": True})

    return await _run(user, _set)


@router.post("/users/{id}/mfa/disable/")
async def disable_user_mfa(request: Request, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Managers turn off a user's two-step sign-in (lost device). The secret is cleared; the user re-enrols."""
    def _disable(s):
        _require(user.role in rules.TOP_ROLES, "Only managers can turn off two-step sign-in.")
        actor = _actor(s, user)
        target = _load_user(s, user.tenant_id, id, lock=True)
        rules.check_not_super_admin(target.role)
        if target.mfa_enabled:
            target.mfa_enabled = False
            target.mfa_secret = ""
            audit(s, user.tenant_id, "admin.user.mfa_disabled", target, actor, {}, request)
        return _respond({"ok": True, "mfa_enabled": False})

    return await _run(user, _disable)


# ---------------------------------------------------------------- teams
def _team_member_ids(s, team_id) -> list:
    return sorted(s.scalars(select(TeamMember.user_id).where(TeamMember.team_id == team_id)))


def _team_account_ids(s, team_id) -> list:
    return sorted(s.scalars(select(TeamAccount.account_id).where(TeamAccount.team_id == team_id)))


def _team_state(s, t):
    return {"name": t.name, "email": t.email, "assignment_strategy": t.assignment_strategy,
            "team_lead_id": t.team_lead_id, "manager_id": t.manager_id,
            "member_ids": _team_member_ids(s, t.id), "account_ids": _team_account_ids(s, t.id)}


# A team names one team lead and one manager; each must be an active user holding that role.
LEAD_FIELDS = {"team_lead_id": "team_lead", "manager_id": "manager"}


def _leaders(s, data, tid):
    out = {}
    for field, role in LEAD_FIELDS.items():
        if field not in data:
            continue
        v = data[field]
        if v in (None, ""):
            out[field] = None
            continue
        if not _is_id(v):
            raise rules.invalid(field.removesuffix("_id").replace("_", " "))
        ok = s.scalars(select(User.id).where(*_users(tid), User.id == v, User.role == role, User.is_active.is_(True))).first()
        if ok is None:
            raise rules.RuleError(400, f"Choose an active user with the {role.replace('_', ' ')} role.")
        out[field] = v
    return out


def _count(s, model, *conds) -> int:
    return s.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _team_usage(s, t):
    open_tickets = s.scalar(select(func.count()).select_from(Ticket).join(State, State.id == Ticket.state_id).where(
        Ticket.tenant_id == t.tenant_id, Ticket.team_id == t.id, State.category.notin_(DONE))) or 0
    return {"open_tickets": open_tickets,
            "tickets": _count(s, Ticket, Ticket.tenant_id == t.tenant_id, Ticket.team_id == t.id),
            "categories": _count(s, Category, Category.tenant_id == t.tenant_id, Category.default_team_id == t.id),
            "catalog_items": _count(s, ServiceCatalogItem, ServiceCatalogItem.tenant_id == t.tenant_id,
                                    ServiceCatalogItem.target_team_id == t.id),
            "services": _count(s, Service, Service.tenant_id == t.tenant_id, Service.owner_team_id == t.id),
            "configuration_items": _count(s, ConfigurationItem, ConfigurationItem.tenant_id == t.tenant_id,
                                          ConfigurationItem.owner_team_id == t.id)}


def _duplicate_name_ids(s, tid):
    seen: dict = {}
    for pk, name in s.execute(select(Team.id, Team.name).where(Team.tenant_id == tid)):
        seen.setdefault(rules.normalize_name(name), []).append(pk)
    return {pk for ids in seen.values() if len(ids) > 1 for pk in ids}


def _person(u):
    return {"id": u.id, "name": u.display_name, "is_active": u.is_active} if u is not None else None


def _team_detail(s, t):
    members = []
    member_rows = s.scalars(select(User).join(TeamMember, TeamMember.user_id == User.id).where(
        TeamMember.team_id == t.id, User.tenant_id == t.tenant_id).order_by(User.first_name, User.last_name, User.email))
    for m in member_rows:
        eligible, reason = rules.member_eligibility(m.role, m.is_active)
        members.append({"id": m.id, "name": m.display_name, "email": m.email, "role": m.role, "is_active": m.is_active,
                        "is_available": m.is_available, "eligible": eligible, "ineligible_reason": reason})
    accounts = s.scalars(select(Account).join(TeamAccount, TeamAccount.account_id == Account.id).where(
        TeamAccount.team_id == t.id).order_by(Account.name))
    usage = _team_usage(s, t)
    version = rules.version_of(_team_state(s, t))
    body = {"id": t.id, "name": t.name, "email": t.email, "assignment_strategy": t.assignment_strategy,
            "team_lead": _person(t.team_lead), "manager": _person(t.manager), "members": members, "accounts": [_ref(a) for a in accounts],
            "usage": usage, "can_delete": not any(usage.values()),
            "duplicate_name": t.id in _duplicate_name_ids(s, t.tenant_id), "version": version}
    return body, version


def _load_team(s, tid, pk, lock=False):
    stmt = select(Team).where(Team.tenant_id == tid, Team.id == pk)
    if lock:
        stmt = stmt.with_for_update()
    t = s.scalars(stmt).first()
    if t is None:
        raise rules.RuleError(404, "Not found.")
    return t


def _name_lock(s, tid):
    # There is no unique constraint on team names, so creates and renames in a tenant are serialised with a
    # transaction-scoped advisory lock and checked case-insensitively.
    s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"opsdesk.team-name.{tid}"})


def _check_unique_name(s, tid, name, exclude=None):
    key = rules.normalize_name(name)
    for pk, other in s.execute(select(Team.id, Team.name).where(Team.tenant_id == tid)):
        if pk != exclude and rules.normalize_name(other) == key:
            raise rules.RuleError(409, "A team with this name already exists.")


def _accounts(s, ids, tid):
    ids = rules.clean_id_list(ids, "account_ids")
    found = list(s.scalars(select(Account).where(Account.tenant_id == tid, Account.id.in_(ids))))
    if len(found) != len(ids):
        raise rules.invalid("account_ids")
    return found


def _members(s, ids, current, tid, field):
    ids = rules.clean_id_list(ids, field)
    found = {u.id: (u.role, u.is_active) for u in s.scalars(select(User).where(*_users(tid), User.id.in_(ids)))}
    try:
        rules.check_members(ids, current, found)
    except rules.RuleError:
        raise rules.invalid(field)
    return ids


def _set_members(s, team_id, ids):
    s.execute(delete(TeamMember).where(TeamMember.team_id == team_id))
    s.add_all([TeamMember(team_id=team_id, user_id=i) for i in ids])
    s.flush()


def _set_accounts(s, team_id, accounts):
    s.execute(delete(TeamAccount).where(TeamAccount.team_id == team_id))
    s.add_all([TeamAccount(team_id=team_id, account_id=a.id) for a in accounts])
    s.flush()


def _require_teams(user):
    _require(rules.can_manage_teams(user.role), "Only managers and team leads can manage teams.")


@router.get("/teams/")
async def list_teams(q: Optional[str] = Query(None, max_length=100), user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        _require_teams(user)
        stmt = select(Team).where(Team.tenant_id == user.tenant_id)
        if q and q.strip():
            stmt = stmt.where(Team.name.ilike(f"%{q.strip()}%"))
        dupes = _duplicate_name_ids(s, user.tenant_id)
        results = []
        for t in s.scalars(stmt.order_by(Team.name, Team.id)):
            members = s.execute(select(User.role, User.is_active).join(TeamMember, TeamMember.user_id == User.id)
                                .where(TeamMember.team_id == t.id)).all()
            ineligible = sum(1 for role, active in members if role not in rules.STAFF_ROLES or not active)
            open_tickets = s.scalar(select(func.count()).select_from(Ticket).join(State, State.id == Ticket.state_id).where(
                Ticket.team_id == t.id, State.category.notin_(DONE))) or 0
            results.append({"id": t.id, "name": t.name, "email": t.email, "assignment_strategy": t.assignment_strategy,
                            "team_lead": _person(t.team_lead), "manager": _person(t.manager),
                            "member_count": len(members), "ineligible_member_count": ineligible,
                            "account_count": _count(s, TeamAccount, TeamAccount.team_id == t.id),
                            "open_ticket_count": open_tickets, "duplicate_name": t.id in dupes})
        return _respond({"count": len(results), "next": None, "previous": None, "results": results})

    return await _run(user, _query, commit=False)


@router.get("/teams/{id}/")
async def get_team(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        _require_teams(user)
        body, version = _team_detail(s, _load_team(s, user.tenant_id, id))
        return _respond(body, version)

    return await _run(user, _query, commit=False)


@router.post("/teams/")
async def create_team(payload: TeamWrite, user: AuthenticatedUser = Depends(get_current_user)):
    sent = payload.model_fields_set
    data = {k: getattr(payload, k) for k in sent}

    def _create(s):
        _require_teams(user)
        clean = rules.clean_team(data, creating=True)
        actor = _actor(s, user)
        _name_lock(s, user.tenant_id)
        _check_unique_name(s, user.tenant_id, clean["name"])
        members = _members(s, data.get("member_ids") or [], set(), user.tenant_id, "member_ids")
        accounts = _accounts(s, data.get("account_ids") or [], user.tenant_id)
        leaders = _leaders(s, data, user.tenant_id)
        t = Team(tenant_id=user.tenant_id, name=clean["name"], email=clean.get("email", ""),
                 assignment_strategy=clean.get("assignment_strategy", "manual"), rr_cursor=0, **leaders)
        s.add(t)
        s.flush()
        _set_members(s, t.id, members)
        _set_accounts(s, t.id, accounts)
        state = _team_state(s, t)
        audit(s, user.tenant_id, "admin.team.create", t, actor, {k: [None, v] for k, v in state.items() if v not in ("", [], None)})
        body, version = _team_detail(s, t)
        return _respond(body, version, code=status.HTTP_201_CREATED)

    return await _run(user, _create)


@router.patch("/teams/{id}/")
async def update_team(payload: TeamPatch, id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                      user: AuthenticatedUser = Depends(get_current_user)):
    sent = payload.model_fields_set
    data = {k: getattr(payload, k) for k in sent}

    def _update(s):
        _require_teams(user)
        clean = rules.clean_team(data, creating=False)
        actor = _actor(s, user)
        if "name" in clean:
            _name_lock(s, user.tenant_id)
        t = _load_team(s, user.tenant_id, id, lock=True)
        before = _team_state(s, t)
        rules.check_if_match(if_match, rules.version_of(before), "team")
        if "name" in clean and rules.normalize_name(clean["name"]) != rules.normalize_name(t.name):
            _check_unique_name(s, user.tenant_id, clean["name"], exclude=t.id)
        accounts = _accounts(s, data["account_ids"], user.tenant_id) if "account_ids" in data else None
        clean.update(_leaders(s, data, user.tenant_id))
        for k, v in clean.items():
            setattr(t, k, v)
        s.flush()
        if accounts is not None:
            _set_accounts(s, t.id, accounts)
        after = _team_state(s, t)
        changes = {k: [before[k], after[k]] for k in after if after[k] != before[k]}
        if changes:
            audit(s, user.tenant_id, "admin.team.update", t, actor, changes)
        body, version = _team_detail(s, t)
        return _respond(body, version)

    return await _run(user, _update)


@router.put("/teams/{id}/members/")
async def replace_members(payload: MembersPut, id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                          user: AuthenticatedUser = Depends(get_current_user)):
    def _replace(s):
        _require_teams(user)
        actor = _actor(s, user)
        t = _load_team(s, user.tenant_id, id, lock=True)
        before = _team_state(s, t)
        rules.check_if_match(if_match, rules.version_of(before), "team")
        ids = _members(s, payload.user_ids, set(before["member_ids"]), user.tenant_id, "user_ids")
        if ids != before["member_ids"]:
            _set_members(s, t.id, ids)
            audit(s, user.tenant_id, "admin.team.members", t, actor, {"member_ids": [before["member_ids"], ids]})
        body, version = _team_detail(s, t)
        return _respond(body, version)

    return await _run(user, _replace)


@router.delete("/teams/{id}/")
async def delete_team(id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                      user: AuthenticatedUser = Depends(get_current_user)):
    """Only a team that no ticket (in any state), category, catalog item, service or configuration item refers to.
    Teams have no archive flag, so a team in use stays and the response says why."""
    def _delete(s):
        _require_teams(user)
        actor = _actor(s, user)
        t = _load_team(s, user.tenant_id, id, lock=True)
        before = _team_state(s, t)
        rules.check_if_match(if_match, rules.version_of(before), "team")
        usage = _team_usage(s, t)
        if any(usage.values()):
            raise rules.RuleError(409, "This team is still in use, so it can't be deleted.", {"usage": usage})
        audit(s, user.tenant_id, "admin.team.delete", t, actor,
              {"name": [before["name"], None], "member_ids": [before["member_ids"], []]})
        s.flush()
        s.delete(t)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return await _run(user, _delete)
