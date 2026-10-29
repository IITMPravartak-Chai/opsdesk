"""Configuration console API.

Everything a tenant manager or team lead configured there is available here, tenant-scoped and audited:

    GET    /api/admin/config/                              schema: resources, field rules, channel kinds, rule vocabulary
    GET    /api/admin/config/options/{model}/              id/label pairs for pickers
    GET    /api/admin/config/{resource}/                   list   (?q=, filters, page, page_size)
    POST   /api/admin/config/{resource}/                   create
    GET    /api/admin/config/{resource}/{id}/              one (with nested children where defined)
    PATCH  /api/admin/config/{resource}/{id}/              update
    DELETE /api/admin/config/{resource}/{id}/              delete (409 with usage counts when something still refers to it)
    .../{parent}/{pid}/{child}/ and .../{child}/{id}/      statuses and transitions of a workflow, custom fields of a type
    GET/PUT /api/admin/config/priority-matrix/             the impact x urgency grid
    GET    /api/admin/config/inbound-messages/             delivery outbox (filter by status/channel)
    POST   /api/admin/config/inbound-messages/reprocess/   requeue failed/ignored messages
    POST   /api/admin/config/sla-policies/  DELETE .../{id}/    managers only
    POST   /api/admin/config/business-hours/ DELETE .../{id}/   managers only

Users, teams, SLA edits and the audit log keep their dedicated endpoints (admin_people, admin_sla, admin_audit).
Every successful write records one ``admin.<resource>.<create|update|delete>`` audit event in the same transaction;
a refused write records none. Secret channel settings are shown masked and kept when the masked value is sent back.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ...channels.registry import adapter_classes
from ...db import models as M
from ...db.base import utcnow
from ...db.models.core import PRIVILEGED_ROLES, TOP_ROLES
from ...db.models.misc import RULE_EVENTS
from ...db.models.workflow import IMPACT_LABELS, URGENCY_LABELS
from ...services import commit_hooks, sla
from ...services.audit import audit
from .. import admin_rules as rules
from ..config_fields import MISSING, collect
from ..config_resources import ACTION_TYPES, CONDITION_OPS, RESOURCES, Resource
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_current_user, require_administration
from . import admin_sla as sla_api

router = APIRouter(prefix="/api/admin/config", tags=["admin-config"], dependencies=[Depends(require_administration)])

DEFAULT_PAGE, MAX_PAGE = 100, 500
OPTION_MODELS = {
    "Workflow": M.Workflow, "State": M.State, "TicketType": M.TicketType, "Priority": M.Priority,
    "Category": M.Category, "Team": M.Team, "Account": M.Account, "Service": M.Service,
    "ConfigurationItem": M.ConfigurationItem, "User": M.User, "SLAPolicy": M.SLAPolicy,
    "BusinessHours": M.BusinessHours, "Channel": M.Channel,
}


# --------------------------------------------------------------------------------------- plumbing
def _ctx(s, user: AuthenticatedUser, parent_id=None) -> SimpleNamespace:
    actor = s.scalars(select(M.User).where(M.User.id == user.user_id, M.User.tenant_id == user.tenant_id,
                                           M.User.is_active.is_(True))).first()
    if actor is None or actor.role not in PRIVILEGED_ROLES:
        raise rules.RuleError(403, "Only managers and team leads can change configuration.")
    return SimpleNamespace(s=s, tid=user.tenant_id, actor=actor, is_admin=actor.role in TOP_ROLES, parent_id=parent_id)


async def _run(user, fn, *, commit=True):
    try:
        if user.role not in PRIVILEGED_ROLES:
            raise rules.RuleError(403, "Only managers and team leads can change configuration.")
        return await run_in_tenant(user, fn, commit=commit)
    except rules.RuleError as e:
        return JSONResponse(status_code=e.status, content={"detail": e.detail, **e.extra})


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(ctx, res: Resource, obj, *, detail: bool, labels: dict) -> dict:
    d = {"id": obj.id}
    for k, f in res.fields.items():
        if f.secret:
            continue
        d[k] = _iso(res.serialize_value(ctx, obj, k, f))
        if f.kind == "fk" and d[k] is not None:
            d[f"{k[:-3]}_label" if k.endswith("_id") else f"{k}_label"] = labels.get(k, {}).get(d[k])
    d.update(res.extra(ctx, obj, detail))
    return d


def _labels(ctx, res: Resource, objs) -> dict:
    out: dict = {}
    for k, f in res.fields.items():
        if f.kind != "fk":
            continue
        ids = {getattr(o, f.attr or k) for o in objs} - {None}
        if ids:
            out[k] = {o.id: (getattr(o, f.label_attr, None) or str(o))
                      for o in ctx.s.scalars(select(f.model).where(f.model.id.in_(ids)))}
    return out


def _load(ctx, res: Resource, id: int, *, lock=False):
    stmt = select(res.model).where(res.model.id == id, *res.scope(ctx.tid, ctx.parent_id))
    if lock:
        stmt = stmt.with_for_update()
    obj = ctx.s.scalars(stmt).first()
    if obj is None:
        raise rules.RuleError(404, "Not found.")
    return obj


def _audit_name(res: Resource, verb: str) -> str:
    return f"admin.{res.slug.replace('-', '_')}.{verb}"


def _diff(before: dict, after: dict) -> dict:
    return {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}


def _list(ctx, res: Resource, q, filters: dict, page: int, page_size: int):
    conds = res.scope(ctx.tid, ctx.parent_id)
    if q and q.strip() and res.search:
        like = f"%{q.strip()}%"
        from sqlalchemy import or_
        conds.append(or_(*[getattr(res.model, c).ilike(like) for c in res.search]))
    for param, col in res.list_filters.items():
        v = filters.get(param)
        if v in (None, ""):
            continue
        column = getattr(res.model, col)
        if v in ("true", "false"):
            conds.append(column.is_(v == "true"))
        elif str(v).lstrip("-").isdigit():
            conds.append(column == int(v))
        else:
            conds.append(column == v)
    count = ctx.s.scalar(select(func.count()).select_from(res.model).where(*conds))
    rows = ctx.s.scalars(select(res.model).where(*conds).order_by(*(res.order or (res.model.id,)), res.model.id)
                         .offset((page - 1) * page_size).limit(page_size)).all()
    labels = _labels(ctx, res, rows)
    return {"count": count, "page": page, "page_size": page_size,
            "results": [_row(ctx, res, o, detail=False, labels=labels) for o in rows]}


def _create(ctx, res: Resource, body: dict, request):
    data = collect(ctx.s, ctx.tid, res.fields, body, partial=False, is_admin=ctx.is_admin)
    data = res.before_create(ctx, data, ctx.parent_id)
    res.validate(ctx, data, None)
    kwargs = {(res.fields[k].attr or k): v for k, v in data.items() if not (res.fields[k].attr or "").startswith("_")
              and k != "depends_on"}
    if hasattr(res.model, "tenant_id"):
        kwargs["tenant_id"] = ctx.tid
    if res.parent:
        kwargs[res.parent[1]] = ctx.parent_id
    obj = res.model(**kwargs)
    ctx.s.add(obj)
    ctx.s.flush()
    res.after_save(ctx, obj, data, True)
    ctx.s.flush()
    ctx.s.refresh(obj)
    audit(ctx.s, ctx.tid, _audit_name(res, "create"), obj, ctx.actor,
          {k: [None, v] for k, v in res.diff_state(ctx, obj).items() if v not in ("", [], {}, None)}, request)
    return obj


def _update(ctx, res: Resource, id: int, body: dict, request):
    obj = _load(ctx, res, id, lock=True)
    data = collect(ctx.s, ctx.tid, res.fields, body, partial=True, is_admin=ctx.is_admin, current=obj)
    before = res.diff_state(ctx, obj)
    res.validate(ctx, data, obj)
    res.apply(ctx, obj, data)
    ctx.s.flush()
    res.after_save(ctx, obj, data, False)
    ctx.s.flush()
    ctx.s.refresh(obj)
    changes = _diff(before, res.diff_state(ctx, obj))
    if changes:
        audit(ctx.s, ctx.tid, _audit_name(res, "update"), obj, ctx.actor, changes, request)
        if res.slug == "accounts" and {"sla_policy_id", "business_hours_id"} & changes.keys():
            audit(ctx.s, ctx.tid, "admin.account.sla_update", obj, ctx.actor,
                  {k: v for k, v in changes.items() if k in ("sla_policy_id", "business_hours_id")}, request)
    return obj


def _delete(ctx, res: Resource, id: int, request):
    obj = _load(ctx, res, id, lock=True)
    res.guard_delete(ctx, obj)
    audit(ctx.s, ctx.tid, _audit_name(res, "delete"), obj, ctx.actor,
          {k: [v, None] for k, v in res.diff_state(ctx, obj).items() if v not in ("", [], {}, None)}, request)
    try:
        with commit_hooks.savepoint(ctx.s):
            ctx.s.delete(obj)
            ctx.s.flush()
    except IntegrityError:
        raise rules.RuleError(409, "This is still in use, so it can't be deleted.")


# --------------------------------------------------------------------------------------- generic routes
def _load_parent(ctx, res: Resource, pid: int):
    parent = RESOURCES[res.parent[0]]
    return _load(SimpleNamespace(**{**ctx.__dict__, "parent_id": None}), parent, pid)


class _Handlers:
    """The five operations of one resource; the route functions below only differ in whether a parent id is present."""

    def __init__(self, res: Resource):
        self.res = res

    async def list(self, request, pid, q, page, page_size, user):
        res = self.res
        filters = {p: request.query_params.get(p) for p in res.list_filters}
        return await _run(user, lambda s: JSONResponse(_list(_ctx(s, user, pid), res, q, filters, page, page_size)),
                          commit=False)

    async def create(self, request, pid, body, user):
        res = self.res

        def _do(s):
            ctx = _ctx(s, user, pid)
            if res.parent:
                _load_parent(ctx, res, pid)
            obj = _create(ctx, res, body, request)
            return JSONResponse(_row(ctx, res, obj, detail=True, labels=_labels(ctx, res, [obj])),
                                status_code=status.HTTP_201_CREATED)
        return await _run(user, _do)

    async def get(self, id, pid, user):
        res = self.res

        def _do(s):
            ctx = _ctx(s, user, pid)
            obj = _load(ctx, res, id)
            return JSONResponse(_row(ctx, res, obj, detail=True, labels=_labels(ctx, res, [obj])))
        return await _run(user, _do, commit=False)

    async def patch(self, request, id, pid, body, user):
        res = self.res

        def _do(s):
            ctx = _ctx(s, user, pid)
            obj = _update(ctx, res, id, body, request)
            return JSONResponse(_row(ctx, res, obj, detail=True, labels=_labels(ctx, res, [obj])))
        return await _run(user, _do)

    async def delete(self, request, id, pid, user):
        res = self.res

        def _do(s):
            _delete(_ctx(s, user, pid), res, id, request)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return await _run(user, _do)


def _register(res: Resource) -> None:
    h = _Handlers(res)
    Q = Query(None, max_length=100)
    PG, PS = Query(1, ge=1), Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE)
    U = Depends(get_current_user)
    if res.parent:
        base = f"/{res.parent[0]}/{{pid}}/{res.slug}"

        async def list_(request: Request, pid: int = Path(..., ge=1), q: Optional[str] = Q, page: int = PG,
                        page_size: int = PS, user: AuthenticatedUser = U):
            return await h.list(request, pid, q, page, page_size, user)

        async def create_(request: Request, body: dict, pid: int = Path(..., ge=1), user: AuthenticatedUser = U):
            return await h.create(request, pid, body, user)

        async def get_(pid: int = Path(..., ge=1), id: int = Path(..., ge=1), user: AuthenticatedUser = U):
            return await h.get(id, pid, user)

        async def patch_(request: Request, body: dict, pid: int = Path(..., ge=1), id: int = Path(..., ge=1),
                         user: AuthenticatedUser = U):
            return await h.patch(request, id, pid, body, user)

        async def delete_(request: Request, pid: int = Path(..., ge=1), id: int = Path(..., ge=1),
                          user: AuthenticatedUser = U):
            return await h.delete(request, id, pid, user)
    else:
        base = f"/{res.slug}"

        async def list_(request: Request, q: Optional[str] = Q, page: int = PG, page_size: int = PS,
                        user: AuthenticatedUser = U):
            return await h.list(request, None, q, page, page_size, user)

        async def create_(request: Request, body: dict, user: AuthenticatedUser = U):
            return await h.create(request, None, body, user)

        async def get_(id: int = Path(..., ge=1), user: AuthenticatedUser = U):
            return await h.get(id, None, user)

        async def patch_(request: Request, body: dict, id: int = Path(..., ge=1), user: AuthenticatedUser = U):
            return await h.patch(request, id, None, body, user)

        async def delete_(request: Request, id: int = Path(..., ge=1), user: AuthenticatedUser = U):
            return await h.delete(request, id, None, user)

    router.add_api_route(base + "/", list_, methods=["GET"], name=f"list_{res.slug}")
    if res.can_create:
        router.add_api_route(base + "/", create_, methods=["POST"], name=f"create_{res.slug}")
    router.add_api_route(base + "/{id}/", get_, methods=["GET"], name=f"get_{res.slug}")
    router.add_api_route(base + "/{id}/", patch_, methods=["PATCH"], name=f"update_{res.slug}")
    if res.can_delete:
        router.add_api_route(base + "/{id}/", delete_, methods=["DELETE"], name=f"delete_{res.slug}")


# --------------------------------------------------------------------------------------- schema + options
@router.get("/")
async def config_schema(user: AuthenticatedUser = Depends(get_current_user)):
    """Everything the console UI needs to render itself: resources and their field rules, channel kinds and their
    documented config keys, and the automation rule vocabulary."""
    return await _run(user, lambda s: JSONResponse({
        "is_admin": user.role in TOP_ROLES,
        "resources": {slug: r.describe() for slug, r in RESOURCES.items()},
        "adapters": {kind: {"label": c.label, "is_pull": c.is_pull, "supports_outbound": c.supports_outbound,
                            "config_help": c.config_help} for kind, c in adapter_classes().items()},
        "rule_events": list(RULE_EVENTS), "condition_ops": list(CONDITION_OPS), "action_types": list(ACTION_TYPES)}),
        commit=False)


@router.get("/options/{model}/")
async def options(model: str = Path(...), parent: Optional[int] = Query(None, ge=1),
                  user: AuthenticatedUser = Depends(get_current_user)):
    """id/label pairs for pickers. ``State`` options can be limited to one workflow with ``?parent=<workflow id>``."""
    def _do(s):
        _ctx(s, user)
        m = OPTION_MODELS.get(model)
        if m is None:
            raise rules.RuleError(404, "Not found.")
        stmt = select(m)
        if hasattr(m, "tenant_id"):
            stmt = stmt.where(m.tenant_id == user.tenant_id)
        elif m is M.State:
            stmt = stmt.where(M.State.workflow_id.in_(select(M.Workflow.id).where(M.Workflow.tenant_id == user.tenant_id)))
        if m is M.State and parent:
            stmt = stmt.where(M.State.workflow_id == parent)
        if m is M.User:
            stmt = stmt.where(M.User.is_superuser.is_(False), M.User.is_active.is_(True))
        order = {M.User: M.User.email, M.Priority: M.Priority.level, M.State: M.State.order}.get(m, getattr(m, "name", m.id))
        rows = s.scalars(stmt.order_by(order, m.id).limit(2000)).all()
        label = (lambda o: o.email) if m is M.User else (lambda o: str(o))
        return JSONResponse([{"id": o.id, "label": label(o)} for o in rows])
    return await _run(user, _do, commit=False)


# --------------------------------------------------------------------------------------- priority matrix
@router.get("/priority-matrix/")
async def get_matrix(user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        _ctx(s, user)
        cells = s.scalars(select(M.PriorityMatrix).where(M.PriorityMatrix.tenant_id == user.tenant_id)
                          .order_by(M.PriorityMatrix.impact, M.PriorityMatrix.urgency)).all()
        return JSONResponse({"cells": [{"impact": c.impact, "urgency": c.urgency, "priority_id": c.priority_id} for c in cells],
                             "impact": IMPACT_LABELS, "urgency": URGENCY_LABELS})
    return await _run(user, _do, commit=False)


class MatrixPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cells: Any


@router.put("/priority-matrix/")
async def put_matrix(payload: MatrixPut, request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """Replace the impact x urgency grid. Every cell needs a priority of this workspace; duplicates are refused."""
    def _do(s):
        ctx = _ctx(s, user)
        if not isinstance(payload.cells, list) or len(payload.cells) > 16:
            raise rules.invalid("cells")
        prio_ids = set(s.scalars(select(M.Priority.id).where(M.Priority.tenant_id == user.tenant_id)))
        seen, rows = set(), []
        for c in payload.cells:
            if not isinstance(c, dict):
                raise rules.invalid("cells")
            i, u, p = c.get("impact"), c.get("urgency"), c.get("priority_id")
            if (isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= 4 or isinstance(u, bool)
                    or not isinstance(u, int) or not 1 <= u <= 4 or p not in prio_ids or (i, u) in seen):
                raise rules.invalid("cells")
            seen.add((i, u))
            rows.append((i, u, p))
        before = {(c.impact, c.urgency): c.priority_id for c in s.scalars(
            select(M.PriorityMatrix).where(M.PriorityMatrix.tenant_id == user.tenant_id))}
        s.execute(delete(M.PriorityMatrix).where(M.PriorityMatrix.tenant_id == user.tenant_id))
        s.add_all([M.PriorityMatrix(tenant_id=user.tenant_id, impact=i, urgency=u, priority_id=p) for i, u, p in rows])
        s.flush()
        after = {(i, u): p for i, u, p in rows}
        changes = {f"{i}x{u}": [before.get((i, u)), after.get((i, u))] for (i, u) in sorted(set(before) | set(after))
                   if before.get((i, u)) != after.get((i, u))}
        if changes:
            audit(s, user.tenant_id, "admin.priority_matrix.update", None, ctx.actor, changes, request)
        return JSONResponse({"cells": [{"impact": i, "urgency": u, "priority_id": p} for i, u, p in sorted(rows)]})
    return await _run(user, _do)


# --------------------------------------------------------------------------------------- inbound messages
@router.get("/inbound-messages/")
async def list_inbound(status_: Optional[str] = Query(None, alias="status"), channel_id: Optional[int] = Query(None, ge=1),
                       page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                       user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        _ctx(s, user)
        conds = [M.InboundMessage.tenant_id == user.tenant_id]
        if status_:
            conds.append(M.InboundMessage.status == status_)
        if channel_id:
            conds.append(M.InboundMessage.channel_id == channel_id)
        count = s.scalar(select(func.count()).select_from(M.InboundMessage).where(*conds))
        rows = s.scalars(select(M.InboundMessage).where(*conds).order_by(M.InboundMessage.received_at.desc())
                         .offset((page - 1) * page_size).limit(page_size)).all()
        names = {c.id: c.name for c in s.scalars(select(M.Channel).where(M.Channel.tenant_id == user.tenant_id))}
        return JSONResponse({"count": count, "page": page, "page_size": page_size, "results": [{
            "id": m.id, "channel_id": m.channel_id, "channel": names.get(m.channel_id), "external_id": m.external_id,
            "status": m.status, "error": m.error, "attempts": m.attempts, "ticket_id": m.ticket_id,
            "received_at": _iso(m.received_at), "last_attempt_at": _iso(m.last_attempt_at),
            "next_attempt_at": _iso(m.next_attempt_at)} for m in rows]})
    return await _run(user, _do, commit=False)


class ReprocessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: Any


@router.post("/inbound-messages/reprocess/")
async def reprocess_inbound(payload: ReprocessBody, request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """Requeue messages with a fresh attempt budget. The worker only processes "queued" rows, so a failed message is
    reset; the reconciler dispatches it even if the broker is unreachable right now. Processed messages are skipped."""
    def _do(s):
        ctx = _ctx(s, user)
        ids = rules.clean_id_list(payload.ids, "ids", limit=200)
        rows = s.scalars(select(M.InboundMessage).where(M.InboundMessage.tenant_id == user.tenant_id,
                                                        M.InboundMessage.id.in_(ids),
                                                        M.InboundMessage.status != "processed")).all()
        now = utcnow()
        for m in rows:
            m.status, m.attempts, m.error, m.next_attempt_at = "queued", 0, "", now
        s.flush()
        from ...worker.tasks import dispatch
        for m in rows:
            dispatch(s, user.tenant_id, m.id)
        if rows:
            audit(s, user.tenant_id, "admin.inbound_message.reprocess", None, ctx.actor, {"ids": [m.id for m in rows]}, request)
        return JSONResponse({"requeued": len(rows)})
    return await _run(user, _do)


# --------------------------------------------------------------------------------------- SLA create / delete
class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    is_default: Any = False
    ticket_type_ids: Any = None
    at_risk_percent: Any = 75
    business_hours_id: Any = None
    targets: Any = None


class HoursCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Any = None
    timezone: Any = "UTC"
    is_24x7: Any = False
    schedule: Any = None
    holidays: Any = None


def _admin_only(s, user):
    return sla_api.actor_admin(s, user)


@router.post("/sla-policies/", status_code=status.HTTP_201_CREATED)
async def create_policy(payload: PolicyCreate, request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """Create an SLA policy with its targets (managers only). The same rules as editing apply: valid targets, a
    reachable resolution time, and no default-policy ambiguity."""
    def _do(s):
        actor = _admin_only(s, user)
        tid = user.tenant_id
        name = payload.name
        if not isinstance(name, str) or not name.strip() or len(" ".join(name.split())) > 120:
            raise rules.invalid("name")
        name = " ".join(name.split())
        if not isinstance(payload.is_default, bool):
            raise rules.invalid("is_default")
        at_risk = rules.clean_at_risk(payload.at_risk_percent)
        type_ids = rules.clean_id_list(payload.ticket_type_ids or [], "ticket_type_ids", limit=100)
        if type_ids and len(set(s.scalars(select(M.TicketType.id).where(M.TicketType.tenant_id == tid,
                                                                         M.TicketType.id.in_(type_ids))))) != len(type_ids):
            raise rules.invalid("ticket_type_ids")
        bh = None
        if payload.business_hours_id is not None:
            v = payload.business_hours_id
            bh = (s.scalars(select(M.BusinessHours).where(M.BusinessHours.tenant_id == tid, M.BusinessHours.id == v)).first()
                  if isinstance(v, int) and not isinstance(v, bool) else None)
            if bh is None:
                raise rules.invalid("business_hours")
            rules.clean_business_hours({}, sla_api.hours_state(bh))
        priority_ids = set(s.scalars(select(M.Priority.id).where(M.Priority.tenant_id == tid)))
        targets = [[t["priority_id"], t["first_response_minutes"], t["resolution_minutes"]]
                   for t in rules.clean_targets(payload.targets or [], priority_ids)]
        existing = sla_api._defaults_data(s, tid)
        new = {"id": 0, "is_default": payload.is_default, "ticket_type_ids": type_ids}
        if rules.new_default_conflicts(existing, existing + [new], 0):
            raise rules.RuleError(409, "Another default policy already applies to these ticket types. Leave this "
                                       "policy's default flag off, or change the other policy first.")
        if bh is not None:
            sla_api._check_reachable(sla.BusinessCalendar.from_model(bh), max([t[2] for t in targets] or [0]), bh.name)
        p = M.SLAPolicy(tenant_id=tid, name=name, is_default=payload.is_default, at_risk_percent=at_risk,
                        business_hours_id=bh.id if bh else None)
        s.add(p)
        s.flush()
        sla_api.set_policy_types(s, p.id, type_ids)
        sla_api.sync_targets(s, p, targets)
        audit(s, tid, "admin.sla_policy.create", p, actor, {
            "name": [None, name], "is_default": [None, p.is_default], "at_risk_percent": [None, at_risk],
            "business_hours_id": [None, p.business_hours_id], "ticket_type_ids": [None, type_ids], "targets": [None, targets],
            "source": "console"}, request)
        s.expire(p)
        body, version = sla_api.policy_detail(s, sla_api.load_policy(s, tid, p.id))
        return sla_api._respond(body, version, code=status.HTTP_201_CREATED)
    return await _run(user, _do)


@router.delete("/sla-policies/{id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(request: Request, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Delete a policy nothing uses (managers only)."""
    def _do(s):
        actor = _admin_only(s, user)
        p = sla_api.load_policy(s, user.tenant_id, id, lock=True)
        used = {"tickets": s.scalar(select(func.count()).select_from(M.Ticket).where(M.Ticket.sla_policy_id == p.id)) or 0,
                "accounts": s.scalar(select(func.count()).select_from(M.Account).where(M.Account.sla_policy_id == p.id)) or 0}
        if any(used.values()):
            raise rules.RuleError(409, "This policy is still in use, so it can't be deleted.", {"usage": used})
        audit(s, user.tenant_id, "admin.sla_policy.delete", p, actor, {"name": [p.name, None], "source": "console"}, request)
        s.flush()
        s.delete(p)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await _run(user, _do)


@router.post("/business-hours/", status_code=status.HTTP_201_CREATED)
async def create_hours(payload: HoursCreate, request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """Create a business-hours calendar (managers only): valid time zone, periods and holidays; a calendar
    without working time must be 24x7."""
    def _do(s):
        actor = _admin_only(s, user)
        base = {"name": "", "timezone": "UTC", "is_24x7": False, "schedule": {}, "holidays": []}
        values = {k: v for k, v in payload.model_dump().items() if v is not None}
        new = rules.clean_business_hours(values, base)
        bh = M.BusinessHours(tenant_id=user.tenant_id, **new)
        s.add(bh)
        s.flush()
        audit(s, user.tenant_id, "admin.business_hours.create", bh, actor,
              {**{k: [None, v] for k, v in sla_api.hours_state(bh).items()}, "source": "console"}, request)
        body, version = sla_api.hours_detail(s, bh)
        return sla_api._respond(body, version, code=status.HTTP_201_CREATED)
    return await _run(user, _do)


@router.delete("/business-hours/{id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hours(request: Request, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Delete a calendar nothing uses (managers only)."""
    def _do(s):
        actor = _admin_only(s, user)
        bh = s.scalars(select(M.BusinessHours).where(M.BusinessHours.tenant_id == user.tenant_id,
                                                     M.BusinessHours.id == id).with_for_update()).first()
        if bh is None:
            raise rules.RuleError(404, "Not found.")
        used = {"policies": s.scalar(select(func.count()).select_from(M.SLAPolicy).where(M.SLAPolicy.business_hours_id == bh.id)) or 0,
                "accounts": s.scalar(select(func.count()).select_from(M.Account).where(M.Account.business_hours_id == bh.id)) or 0}
        if any(used.values()):
            raise rules.RuleError(409, "These business hours are still in use, so they can't be deleted.", {"usage": used})
        audit(s, user.tenant_id, "admin.business_hours.delete", bh, actor, {"name": [bh.name, None], "source": "console"}, request)
        s.flush()
        s.delete(bh)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await _run(user, _do)


# Generic CRUD routes are registered last so the fixed paths above (priority-matrix, inbound-messages, options,
# sla-policies, business-hours) are matched first.
for _res in RESOURCES.values():
    if not _res.parent:
        _register(_res)
for _res in RESOURCES.values():
    if _res.parent:
        _register(_res)
