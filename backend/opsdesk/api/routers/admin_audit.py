"""Read-only Audit Log API (U7-A1).

GET /api/admin/audit-logs/          events of the caller's tenant, newest first (created_at, then id)
GET /api/admin/audit-logs/actions/  the action names that occur in the caller's tenant (for the filter list)

Managers only (provisional owner decision U7-0 D-5); the role is re-read from the database on every request by
get_current_user. The tenant comes from the caller's signed token, never from a parameter, and is enforced twice: by
the signed tenant context (forced row-level security on core_auditlog) and by an explicit tenant_id filter.

There are deliberately no write routes: audit events cannot be created, changed or deleted through this API (any other
method gets 405). What a response omits:
- ip_address: audit() takes the first X-Forwarded-For entry, which a client can set (nginx appends with
  $proxy_add_x_forwarded_for), and the FastAPI writers store none, so no reliable value exists to show.
- the actor's email and every other user field except a display name.
- credential-like keys inside `changes` (redacted) and long text (truncated); see audit_view.sanitize.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ...db.models import AuditLog

from .. import audit_view
from ..security import AuthenticatedUser, get_current_user, require_administration
from ..runtime import run_in_tenant

router = APIRouter(prefix="/api/admin/audit-logs", tags=["admin-audit-log"], dependencies=[Depends(require_administration)])

FORBIDDEN = "Only managers can view the audit log."


def _require_reader(user: AuthenticatedUser):
    if not audit_view.can_read(user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)


def _conditions(lookups: dict) -> list:
    """Translate the validated filter lookups (see audit_view.parse_filters) into SQLAlchemy criteria."""
    ops = {"action": lambda v: AuditLog.action == v, "action__startswith": lambda v: AuditLog.action.startswith(v, autoescape=True),
           "actor_id": lambda v: AuditLog.actor_id == v, "object_type": lambda v: AuditLog.object_type == v,
           "object_id": lambda v: AuditLog.object_id == v, "created_at__gte": lambda v: AuditLog.created_at >= v,
           "created_at__lt": lambda v: AuditLog.created_at < v}
    return [ops[k](v) for k, v in lookups.items()]


def _bad(field):
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field}.")


def _event(row):
    changes, truncated = audit_view.sanitize(row.changes or {})
    actor = row.actor
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
        "action": row.action,
        "actor": {"id": actor.id, "name": actor.display_name} if actor is not None else None,
        "object_type": row.object_type or None,
        "object_id": row.object_id or None,
        "changes": changes,
        "changes_truncated": truncated,
    }


@router.get("/")
async def list_audit_events(
    request: Request,
    action: Optional[str] = Query(None, description="Exact action, e.g. ticket.update"),
    action_prefix: Optional[str] = Query(None, description="Action family, e.g. ticket. or auth."),
    actor: Optional[str] = Query(None, description="Actor user id"),
    object_type: Optional[str] = Query(None, description="e.g. Ticket, User"),
    object_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO 8601; no offset means UTC; inclusive"),
    until: Optional[str] = Query(None, description="ISO 8601; no offset means UTC; exclusive"),
    page: Optional[str] = Query(None),
    page_size: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    _require_reader(user)
    raw = {"action": action, "action_prefix": action_prefix, "actor": actor, "object_type": object_type,
           "object_id": object_id, "since": since, "until": until}
    try:
        lookups = audit_view.parse_filters(raw)
        page_no, size, offset = audit_view.parse_page(page, page_size)
    except audit_view.InvalidFilter as e:
        raise _bad(e.field)

    def _query(s):
        conds = _conditions(lookups) + [AuditLog.tenant_id == user.tenant_id]
        count = s.scalar(select(func.count()).select_from(AuditLog).where(*conds))
        rows = s.scalars(select(AuditLog).options(joinedload(AuditLog.actor)).where(*conds)
                         .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(size)).unique()
        return count, [_event(r) for r in rows]

    count, results = await run_in_tenant(user, _query, commit=False)
    base = f"{request.url.scheme}://{request.url.netloc}{request.url.path}"
    nxt, prev = audit_view.page_links(base, raw, page_no, size, count)
    return {"count": count, "next": nxt, "previous": prev, "page": page_no, "page_size": size, "results": results}


@router.get("/actions/")
async def list_audit_actions(user: AuthenticatedUser = Depends(get_current_user)):
    _require_reader(user)

    def _query(s):
        return list(s.scalars(select(AuditLog.action).where(AuditLog.tenant_id == user.tenant_id)
                              .distinct().order_by(AuditLog.action).limit(200)))

    return {"actions": await run_in_tenant(user, _query, commit=False)}
