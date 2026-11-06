"""SLA configuration API. Managers only, for reading and writing.

GET   /api/admin/sla-policies/            policies, with any default-policy ambiguity the engine would resolve arbitrarily
GET   /api/admin/sla-policies/{id}/       one policy with its targets and the choices for editing; `version` and ETag
PATCH /api/admin/sla-policies/{id}/       name, default flag, ticket types, at-risk threshold, business hours, targets
GET   /api/admin/business-hours/          calendars and what uses them
GET   /api/admin/business-hours/{id}/     one calendar; `version` and ETag
PATCH /api/admin/business-hours/{id}/     name, time zone, 24x7, weekly schedule, holidays
(create and delete of both live in admin_config.py)

SLA semantics are those of services/sla.py:
- A write changes configuration only. No ticket is saved, no due date is moved and no notification is sent here.
  Stamped due dates change only when the engine re-bases them (a ticket's priority or account changes).
- At-risk thresholds and calendars are read live by the SLA monitor (every 60 s), so a change can flip active tickets
  to "at risk" at its next run and notify their assignees. Such a write therefore needs `confirm_impact: true` when
  active tickets use the policy or calendar; without it the response is 409 with an `impact` preview computed by the
  engine's own evaluate() on detached snapshots.
- A configuration the engine cannot compute is refused: a calendar without working time (not 24x7), or a resolution
  target that the calendar cannot reach within the engine's 800-day scan (ticket creation would fail).
- A write may not create or widen a default-policy ambiguity; ambiguities that already exist are reported, not fixed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from ...db.base import utcnow
from ...db.models import (Account, BusinessHours, Priority, SLAPolicy, SLAPolicyTicketType, SLATarget, State, Ticket,
                          TicketType, User)
from ...services import sla
from ...services.audit import audit
from .. import admin_rules as rules
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_current_user, require_administration

router = APIRouter(prefix="/api/admin", tags=["admin-sla"], dependencies=[Depends(require_administration)])

FORBIDDEN = "Only managers can view or change SLA configuration."
SIMULATION_LIMIT = 5000
DONE = ("resolved", "closed", "cancelled")


class PolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    is_default: Any = None
    ticket_type_ids: Any = None
    at_risk_percent: Any = None
    business_hours_id: Any = None
    targets: Any = None
    confirm_impact: Any = None


class HoursPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    timezone: Any = None
    is_24x7: Any = None
    schedule: Any = None
    holidays: Any = None
    confirm_impact: Any = None


def _respond(body, version=None, code=200):
    return JSONResponse(status_code=code, content=body, headers={"ETag": f'"{version}"'} if version else None)


async def _run(user, fn, *, commit=True):
    try:
        if not rules.can_manage_sla(user.role):
            raise rules.RuleError(403, FORBIDDEN)
        return await run_in_tenant(user, fn, commit=commit)
    except rules.RuleError as e:
        return JSONResponse(status_code=e.status, content={"detail": e.detail, **e.extra})


def actor_admin(s: Session, user: AuthenticatedUser) -> User:
    a = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id,
                                     User.is_active.is_(True), User.role.in_(rules.TOP_ROLES))).first()
    if a is None:  # deactivated or demoted since the request was authorised
        raise rules.RuleError(403, FORBIDDEN)
    return a


# ---------------------------------------------------------------- shared
def _active_stmt(tid):
    return select(Ticket).join(State, State.id == Ticket.state_id).where(Ticket.tenant_id == tid, sla.active_sla_clause())


def _count(s, stmt) -> int:
    return s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0


def _paused_stmt(tid):
    return (select(Ticket).join(State, State.id == Ticket.state_id)
            .where(Ticket.tenant_id == tid, Ticket.sla_status == "paused", State.category.notin_(DONE)))


def _uses_calendar(bh):
    """Tickets whose SLA calendar is `bh`, exactly as sla.calendar_for chooses it: the policy's business hours, or the
    account's when the policy has none."""
    with_bh = select(SLAPolicy.id).where(SLAPolicy.business_hours_id == bh.id)
    without_bh = select(SLAPolicy.id).where(SLAPolicy.business_hours_id.is_(None))
    acct = select(Account.id).where(Account.business_hours_id == bh.id)
    return or_(Ticket.sla_policy_id.in_(with_bh),
               and_(or_(Ticket.sla_policy_id.is_(None), Ticket.sla_policy_id.in_(without_bh)), Ticket.account_id.in_(acct)))


def _snapshot(t: Ticket) -> SimpleNamespace:
    """A detached copy carrying what sla.evaluate()/calendar_for() read, so simulating never touches the session."""
    pol = t.sla_policy
    acct = t.account
    return SimpleNamespace(
        sla_status=t.sla_status, state=SimpleNamespace(is_done=t.state.is_done), first_responded_at=t.first_responded_at,
        response_due_at=t.response_due_at, response_breached=t.response_breached, resolution_due_at=t.resolution_due_at,
        resolution_breached=t.resolution_breached, created_at=t.created_at, sla_policy_id=t.sla_policy_id,
        sla_policy=SimpleNamespace(at_risk_percent=pol.at_risk_percent, business_hours_id=pol.business_hours_id,
                                   business_hours=pol.business_hours) if pol else None,
        account_id=t.account_id,
        account=SimpleNamespace(business_hours_id=acct.business_hours_id, business_hours=acct.business_hours) if acct else None)


def _simulate(s: Session, stmt, mutate):
    """Count how the next SLA monitor run would re-classify the tickets of `stmt` if the change were saved."""
    now = utcnow()
    total = _count(s, stmt)
    out = {"active_tickets": total, "to_at_risk": 0, "to_on_track": 0, "sampled": total > SIMULATION_LIMIT}
    rows = s.scalars(stmt.options(joinedload(Ticket.state), joinedload(Ticket.sla_policy).joinedload(SLAPolicy.business_hours),
                                  joinedload(Ticket.account).joinedload(Account.business_hours))
                     .limit(SIMULATION_LIMIT)).unique()
    for t in rows:
        snap = _snapshot(t)
        current = sla.evaluate(snap, now)
        mutate(snap)
        new = sla.evaluate(snap, now)
        if new != current and new == "at_risk":
            out["to_at_risk"] += 1
        elif new != current and new == "on_track":
            out["to_on_track"] += 1
    return out


def _check_reachable(cal, minutes, label):
    if not minutes:
        return
    try:
        cal.add_minutes(utcnow(), minutes)
    except ValueError:
        raise rules.RuleError(400, f"{label}: a resolution target of {minutes} minutes can't be reached within these "
                                   "business hours (the SLA engine looks ahead 800 days), so new tickets would fail.")


def _max_resolution(s, policy_ids):
    if not policy_ids:
        return 0
    return s.scalar(select(func.max(SLATarget.resolution_minutes)).where(SLATarget.policy_id.in_(policy_ids))) or 0


def _hours_ref(bh):
    return {"id": bh.id, "name": bh.name, "is_24x7": bh.is_24x7} if bh else None


# ---------------------------------------------------------------- policies
def _type_ids(s, policy_id):
    return sorted(s.scalars(select(SLAPolicyTicketType.tickettype_id).where(SLAPolicyTicketType.slapolicy_id == policy_id)))


def _policy_state(s, p):
    targets = s.scalars(select(SLATarget).where(SLATarget.policy_id == p.id).order_by(SLATarget.priority_id))
    return {"name": p.name, "is_default": p.is_default, "at_risk_percent": p.at_risk_percent,
            "business_hours_id": p.business_hours_id, "ticket_type_ids": _type_ids(s, p.id),
            "targets": [[t.priority_id, t.first_response_minutes, t.resolution_minutes] for t in targets]}


def _defaults_data(s, tid, override=None):
    data = []
    for p in s.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id == tid)):
        d = {"id": p.id, "is_default": p.is_default, "ticket_type_ids": _type_ids(s, p.id)}
        if override and override["id"] == p.id:
            d.update(override)
        data.append(d)
    return data


def _named_conflicts(s, tid, conflicts):
    types = dict(s.execute(select(TicketType.id, TicketType.name).where(TicketType.tenant_id == tid)).all())
    names = dict(s.execute(select(SLAPolicy.id, SLAPolicy.name).where(SLAPolicy.tenant_id == tid)).all())
    return [{"ticket_type": {"id": c["ticket_type_id"], "name": types.get(c["ticket_type_id"])} if c["ticket_type_id"] else None,
             "scope": types.get(c["ticket_type_id"], "Unknown type") if c["ticket_type_id"] else "All ticket types",
             "policies": [{"id": pid, "name": names.get(pid)} for pid in c["policy_ids"]]} for c in conflicts]


def policy_row(s, p):
    return {"id": p.id, "name": p.name, "is_default": p.is_default, "at_risk_percent": p.at_risk_percent,
            "business_hours": _hours_ref(p.business_hours),
            "ticket_types": [{"id": tt.id, "name": tt.name} for tt in sorted(p.ticket_types, key=lambda x: x.name)],
            "target_count": s.scalar(select(func.count()).select_from(SLATarget).where(SLATarget.policy_id == p.id)),
            "active_ticket_count": _count(s, _active_stmt(p.tenant_id).where(Ticket.sla_policy_id == p.id)),
            "account_count": s.scalar(select(func.count()).select_from(Account).where(
                Account.tenant_id == p.tenant_id, Account.sla_policy_id == p.id))}


def policy_detail(s, p):
    tid = p.tenant_id
    body = policy_row(s, p)
    targets = {t.priority_id: t for t in s.scalars(select(SLATarget).where(SLATarget.policy_id == p.id))}
    body["targets"] = [{"priority": {"id": pr.id, "key": pr.key, "name": pr.name, "level": pr.level},
                        "first_response_minutes": targets[pr.id].first_response_minutes if pr.id in targets else None,
                        "resolution_minutes": targets[pr.id].resolution_minutes if pr.id in targets else None}
                       for pr in s.scalars(select(Priority).where(Priority.tenant_id == tid).order_by(Priority.level, Priority.id))]
    body["paused_ticket_count"] = _count(s, _paused_stmt(tid).where(Ticket.sla_policy_id == p.id))
    body["options"] = {"ticket_types": [{"id": pk, "name": n} for pk, n in s.execute(
                           select(TicketType.id, TicketType.name).where(TicketType.tenant_id == tid)
                           .order_by(TicketType.order, TicketType.name)).all()],
                       "business_hours": [_hours_ref(bh) for bh in s.scalars(
                           select(BusinessHours).where(BusinessHours.tenant_id == tid).order_by(BusinessHours.name))]}
    body["default_conflicts"] = [c for c in _named_conflicts(s, tid, rules.default_conflicts(_defaults_data(s, tid)))
                                 if any(x["id"] == p.id for x in c["policies"])]
    version = rules.version_of(_policy_state(s, p))
    body["version"] = version
    return body, version


def load_policy(s, tid, pk, lock=False):
    stmt = select(SLAPolicy).where(SLAPolicy.tenant_id == tid, SLAPolicy.id == pk)
    if lock:
        stmt = stmt.with_for_update()
    p = s.scalars(stmt).first()
    if p is None:
        raise rules.RuleError(404, "Not found.")
    return p


def set_policy_types(s, policy_id, ids):
    s.execute(delete(SLAPolicyTicketType).where(SLAPolicyTicketType.slapolicy_id == policy_id))
    s.add_all([SLAPolicyTicketType(slapolicy_id=policy_id, tickettype_id=i) for i in ids])
    s.flush()


def sync_targets(s, p, targets):
    wanted = {t[0]: t for t in targets}
    s.execute(delete(SLATarget).where(SLATarget.policy_id == p.id,
                                      SLATarget.priority_id.notin_(list(wanted) or [-1])))
    existing = {t.priority_id: t for t in s.scalars(select(SLATarget).where(SLATarget.policy_id == p.id))}
    for pid, fr, res in wanted.values():
        if pid in existing:
            existing[pid].first_response_minutes, existing[pid].resolution_minutes = fr, res
        else:
            s.add(SLATarget(policy_id=p.id, priority_id=pid, first_response_minutes=fr, resolution_minutes=res))
    s.flush()


@router.get("/sla-policies/")
async def list_policies(user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        pols = s.scalars(select(SLAPolicy).options(joinedload(SLAPolicy.business_hours)).where(
            SLAPolicy.tenant_id == user.tenant_id).order_by(SLAPolicy.name, SLAPolicy.id)).unique().all()
        rows = [policy_row(s, p) for p in pols]
        conflicts = _named_conflicts(s, user.tenant_id, rules.default_conflicts(_defaults_data(s, user.tenant_id)))
        return _respond({"count": len(rows), "next": None, "previous": None, "results": rows, "default_conflicts": conflicts})

    return await _run(user, _query, commit=False)


@router.get("/sla-policies/{id}/")
async def get_policy(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        body, version = policy_detail(s, load_policy(s, user.tenant_id, id))
        return _respond(body, version)

    return await _run(user, _query, commit=False)


@router.patch("/sla-policies/{id}/")
async def update_policy(payload: PolicyPatch, id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                        user: AuthenticatedUser = Depends(get_current_user)):
    sent = payload.model_fields_set
    data = {k: getattr(payload, k) for k in sent}

    def _update(s):
        actor = actor_admin(s, user)
        tid = user.tenant_id
        p = load_policy(s, tid, id, lock=True)
        before = _policy_state(s, p)
        rules.check_if_match(if_match, rules.version_of(before), "SLA policy")
        new = {k: (list(v) if isinstance(v, list) else v) for k, v in before.items()}
        if "name" in data:
            v = data["name"]
            if not isinstance(v, str) or not v.strip() or len(" ".join(v.split())) > 120:
                raise rules.invalid("name")
            new["name"] = " ".join(v.split())
        if "is_default" in data:
            if not isinstance(data["is_default"], bool):
                raise rules.invalid("is_default")
            new["is_default"] = data["is_default"]
        if "at_risk_percent" in data:
            new["at_risk_percent"] = rules.clean_at_risk(data["at_risk_percent"])
        if "ticket_type_ids" in data:
            ids = rules.clean_id_list(data["ticket_type_ids"], "ticket_type_ids", limit=100)
            if _count(s, select(TicketType.id).where(TicketType.tenant_id == tid, TicketType.id.in_(ids))) != len(ids):
                raise rules.invalid("ticket_type_ids")
            new["ticket_type_ids"] = ids
        bh = p.business_hours
        if "business_hours_id" in data:
            v = data["business_hours_id"]
            if v is None:
                bh = None
            else:
                bh = (s.scalars(select(BusinessHours).where(BusinessHours.tenant_id == tid, BusinessHours.id == v)).first()
                      if isinstance(v, int) and not isinstance(v, bool) else None)
                if bh is None:
                    raise rules.invalid("business_hours")
                rules.clean_business_hours({}, hours_state(bh))  # refuses a stored calendar without working time
            new["business_hours_id"] = bh.id if bh else None
        if "targets" in data:
            priority_ids = set(s.scalars(select(Priority.id).where(Priority.tenant_id == tid)))
            new["targets"] = [[t["priority_id"], t["first_response_minutes"], t["resolution_minutes"]]
                              for t in rules.clean_targets(data["targets"], priority_ids)]
        if "confirm_impact" in data and not isinstance(data["confirm_impact"], bool):
            raise rules.invalid("confirm_impact")

        # Defaults: a write may not make the engine's choice arbitrary in a new place.
        override = {"id": p.id, "is_default": new["is_default"], "ticket_type_ids": new["ticket_type_ids"]}
        clash = rules.new_default_conflicts(_defaults_data(s, tid), _defaults_data(s, tid, override), p.id)
        if clash:
            named = _named_conflicts(s, tid, clash)
            raise rules.RuleError(409, f"Another default policy already applies to {named[0]['scope']}. "
                                       "Remove the default flag from one of them first.", {"conflicts": named})

        # The engine must be able to compute every due date this policy can produce.
        max_res = max([t[2] for t in new["targets"]] or [0])
        if bh is not None:
            _check_reachable(sla.BusinessCalendar.from_model(bh), max_res, bh.name)
        else:
            for acc_bh in s.scalars(select(BusinessHours).where(
                    BusinessHours.tenant_id == tid, BusinessHours.id.in_(select(Account.business_hours_id).where(
                        Account.business_hours_id.is_not(None))))):
                _check_reachable(sla.BusinessCalendar.from_model(acc_bh), max_res, acc_bh.name)

        # Live-read settings: confirm before anything that the monitor would apply to active tickets.
        live = new["at_risk_percent"] != before["at_risk_percent"] or new["business_hours_id"] != before["business_hours_id"]
        if live and data.get("confirm_impact") is not True:
            def mutate(t):
                t.sla_policy.at_risk_percent = new["at_risk_percent"]
                t.sla_policy.business_hours = bh
                t.sla_policy.business_hours_id = bh.id if bh else None

            impact = _simulate(s, _active_stmt(tid).where(Ticket.sla_policy_id == p.id), mutate)
            impact["paused_tickets"] = _count(s, _paused_stmt(tid).where(Ticket.sla_policy_id == p.id))
            if impact["active_tickets"] or impact["paused_tickets"]:
                raise rules.RuleError(409, "This change affects tickets that are being timed now. Review the impact "
                                           "and confirm.", {"impact": impact})

        p.name, p.is_default, p.at_risk_percent, p.business_hours_id = (
            new["name"], new["is_default"], new["at_risk_percent"], bh.id if bh else None)
        s.flush()
        if new["ticket_type_ids"] != before["ticket_type_ids"]:
            set_policy_types(s, p.id, new["ticket_type_ids"])
        if new["targets"] != before["targets"]:
            sync_targets(s, p, new["targets"])
        s.expire(p)
        after = _policy_state(s, p)
        changes = {k: [before[k], after[k]] for k in after if after[k] != before[k]}
        if changes:
            audit(s, tid, "admin.sla_policy.update", p, actor, changes)
        s.flush()
        body, version = policy_detail(s, load_policy(s, tid, id))
        return _respond(body, version)

    return await _run(user, _update)


# ---------------------------------------------------------------- business hours
def hours_state(bh):
    return {"name": bh.name, "timezone": bh.timezone, "is_24x7": bh.is_24x7, "schedule": bh.schedule or {},
            "holidays": list(bh.holidays or [])}


def _hours_valid(bh):
    try:
        rules.clean_business_hours({}, hours_state(bh))
        return True
    except rules.RuleError:
        return False


def hours_detail(s, bh):
    tid = bh.tenant_id
    state = hours_state(bh)
    body = {"id": bh.id, **state, "valid": _hours_valid(bh),
            "policies": [{"id": pk, "name": n} for pk, n in s.execute(
                select(SLAPolicy.id, SLAPolicy.name).where(SLAPolicy.tenant_id == tid, SLAPolicy.business_hours_id == bh.id)
                .order_by(SLAPolicy.name)).all()],
            "account_count": s.scalar(select(func.count()).select_from(Account).where(
                Account.tenant_id == tid, Account.business_hours_id == bh.id)),
            "active_ticket_count": _count(s, _active_stmt(tid).where(_uses_calendar(bh)))}
    version = rules.version_of(state)
    body["version"] = version
    return body, version


@router.get("/business-hours/")
async def list_hours(user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        rows = []
        for bh in s.scalars(select(BusinessHours).where(BusinessHours.tenant_id == user.tenant_id)
                            .order_by(BusinessHours.name, BusinessHours.id)):
            body, _ = hours_detail(s, bh)
            body.pop("version")
            rows.append(body)
        return _respond({"count": len(rows), "next": None, "previous": None, "results": rows})

    return await _run(user, _query, commit=False)


@router.get("/business-hours/{id}/")
async def get_hours(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        bh = s.scalars(select(BusinessHours).where(BusinessHours.tenant_id == user.tenant_id, BusinessHours.id == id)).first()
        if bh is None:
            raise rules.RuleError(404, "Not found.")
        body, version = hours_detail(s, bh)
        return _respond(body, version)

    return await _run(user, _query, commit=False)


@router.patch("/business-hours/{id}/")
async def update_hours(payload: HoursPatch, id: int = Path(..., ge=1), if_match: Optional[str] = Header(None),
                       user: AuthenticatedUser = Depends(get_current_user)):
    sent = payload.model_fields_set
    data = {k: getattr(payload, k) for k in sent}

    def _update(s):
        actor = actor_admin(s, user)
        tid = user.tenant_id
        bh = s.scalars(select(BusinessHours).where(BusinessHours.tenant_id == tid, BusinessHours.id == id)
                       .with_for_update()).first()
        if bh is None:
            raise rules.RuleError(404, "Not found.")
        before = hours_state(bh)
        rules.check_if_match(if_match, rules.version_of(before), "business hours")
        if "confirm_impact" in data and not isinstance(data["confirm_impact"], bool):
            raise rules.invalid("confirm_impact")
        new = rules.clean_business_hours({k: v for k, v in data.items() if k != "confirm_impact"}, before)
        candidate = BusinessHours(id=bh.id, tenant_id=tid, **new)  # transient: never added to the session
        cal = sla.BusinessCalendar.from_model(candidate)

        # Every policy that can use this calendar must still be computable (see _uses_calendar).
        direct = list(s.scalars(select(SLAPolicy.id).where(SLAPolicy.tenant_id == tid, SLAPolicy.business_hours_id == bh.id)))
        _check_reachable(cal, _max_resolution(s, direct), new["name"])
        if s.scalar(select(func.count()).select_from(Account).where(Account.tenant_id == tid, Account.business_hours_id == bh.id)):
            calendarless = list(s.scalars(select(SLAPolicy.id).where(SLAPolicy.tenant_id == tid,
                                                                     SLAPolicy.business_hours_id.is_(None))))
            _check_reachable(cal, _max_resolution(s, calendarless), new["name"])

        live = {k: new[k] for k in ("timezone", "is_24x7", "schedule", "holidays")} != \
               {k: before[k] for k in ("timezone", "is_24x7", "schedule", "holidays")}
        if live and data.get("confirm_impact") is not True:
            def mutate(t):
                copy = BusinessHours(id=bh.id, tenant_id=tid, **new)
                if t.sla_policy is not None and t.sla_policy.business_hours_id == bh.id:
                    t.sla_policy.business_hours = copy
                elif t.account is not None and t.account.business_hours_id == bh.id:
                    t.account.business_hours = copy

            impact = _simulate(s, _active_stmt(tid).where(_uses_calendar(bh)), mutate)
            impact["paused_tickets"] = _count(s, _paused_stmt(tid).where(_uses_calendar(bh)))
            if impact["active_tickets"] or impact["paused_tickets"]:
                raise rules.RuleError(409, "This change affects tickets that are being timed now. Review the impact "
                                           "and confirm.", {"impact": impact})

        for k, v in new.items():
            setattr(bh, k, v)
        s.flush()
        after = hours_state(bh)
        changes = {k: [before[k], after[k]] for k in after if after[k] != before[k]}
        if changes:
            audit(s, tid, "admin.business_hours.update", bh, actor, changes)
        body, version = hours_detail(s, bh)
        return _respond(body, version)

    return await _run(user, _update)
