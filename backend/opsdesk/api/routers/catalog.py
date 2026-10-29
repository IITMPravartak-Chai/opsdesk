"""Employee service catalog, pending approvals and saved queue filters.

These endpoints were missing from the first FastAPI cut-over; they are implemented here.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from ...db.models import Approval, SavedQueue, ServiceCatalogItem, Ticket, User
from ...db.models.core import PRIVILEGED_ROLES, TOP_ROLES
from ...services import tickets as services
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_authenticated_user, get_current_user

router = APIRouter(tags=["catalog"])

VALID_SAVED_QUEUE_PARAMS = {
    "assignee", "team", "account", "priority", "type", "state_category",
    "state", "sla_status", "source", "major", "open", "q", "ordering", "mine",
}


def _forbid(detail: str, code: int = status.HTTP_403_FORBIDDEN) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _me(s, user: AuthenticatedUser) -> User:
    u = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id,
                                     User.is_active.is_(True))).first()
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or not found.")
    return u


# --------------------------------------------------------------------------- catalog
def _catalog_item(o: ServiceCatalogItem) -> dict:
    return {"id": o.id, "name": o.name, "description": o.description, "icon": o.icon,
            "category": {"id": o.category_id, "name": str(o.category)} if o.category_id else None,
            "target_team": {"id": o.target_team_id, "name": o.target_team.name} if o.target_team_id else None,
            "requires_approval": o.requires_approval, "approval_type": o.approval_type,
            "form_schema": o.form_schema, "order": o.order}


def _catalog_gate(me: User) -> None:
    if me.role == "customer":
        raise _forbid("Service catalog is accessible to staff only.")


def _catalog_stmt(user: AuthenticatedUser):
    return (select(ServiceCatalogItem).options(joinedload(ServiceCatalogItem.category), joinedload(ServiceCatalogItem.target_team),
                                               joinedload(ServiceCatalogItem.ticket_type))
            .where(ServiceCatalogItem.tenant_id == user.tenant_id, ServiceCatalogItem.is_active.is_(True))
            .order_by(ServiceCatalogItem.order, ServiceCatalogItem.name))


@router.get("/api/catalog/")
async def list_catalog(user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _do(s):
        _catalog_gate(_me(s, user))
        return [_catalog_item(o) for o in s.scalars(_catalog_stmt(user)).unique()]

    return await run_in_tenant(user, _do, commit=False)


@router.get("/api/catalog/{id}/")
async def get_catalog_item(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    def _do(s):
        _catalog_gate(_me(s, user))
        o = s.scalars(_catalog_stmt(user).where(ServiceCatalogItem.id == id)).unique().first()
        if not o:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        return _catalog_item(o)

    return await run_in_tenant(user, _do, commit=False)


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fields: Any = Field(default_factory=dict)
    additional_notes: str = ""


@router.post("/api/catalog/{id}/request/", status_code=status.HTTP_201_CREATED)
async def request_catalog_item(payload: CatalogRequest, id: int = Path(..., ge=1),
                               user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Raise a service request from a catalog item: validates the form, routes the approval and notifies the approver."""
    def _do(s):
        me = _me(s, user)
        _catalog_gate(me)
        item = s.scalars(_catalog_stmt(user).where(ServiceCatalogItem.id == id)).unique().first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        ticket, approver = services.create_service_request_from_catalog(s, item, me, payload.fields or {},
                                                                        payload.additional_notes or "")
        return {"id": ticket.id, "number": ticket.number, "subject": ticket.subject, "status": ticket.state.name,
                "state_key": ticket.state.key, "requires_approval": item.requires_approval,
                "approver": {"id": approver.id, "name": approver.display_name} if approver else None}

    return await run_in_tenant(user, _do)


# --------------------------------------------------------------------------- approvals
def _person(u: Optional[User], with_dept: bool = False):
    if not u:
        return None
    out = {"id": u.id, "name": u.display_name, "email": u.email}
    if with_dept:
        out["department"] = u.department.name if getattr(u, "department_id", None) else None
        out["job_title"] = u.job_title
    return out


@router.get("/api/approvals/")
async def list_pending_approvals(user: AuthenticatedUser = Depends(get_authenticated_user)):
    """The caller's pending approvals, newest first."""
    def _do(s):
        rows = s.scalars(
            select(Approval).options(joinedload(Approval.ticket).joinedload(Ticket.state),
                                     joinedload(Approval.ticket).joinedload(Ticket.requester).joinedload(User.department),
                                     joinedload(Approval.approver))
            .where(Approval.tenant_id == user.tenant_id, Approval.approver_id == user.user_id, Approval.status == "pending")
            .order_by(Approval.created_at.desc())).unique()
        out = []
        for o in rows:
            req = _person(o.ticket.requester, with_dept=True)
            out.append({"id": o.id,
                        "ticket": {"id": o.ticket_id, "number": o.ticket.number, "subject": o.ticket.subject,
                                   "state": o.ticket.state.name, "requester": req},
                        "requester": req, "approver": _person(o.approver), "status": o.status,
                        "created_at": o.created_at.isoformat() if o.created_at else None})
        return out

    return await run_in_tenant(user, _do, commit=False)


# --------------------------------------------------------------------------- saved queues
class SavedQueueIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    is_shared: Optional[bool] = None
    params: Optional[Any] = None


def _validate_params(value) -> dict:
    if not isinstance(value, dict):
        raise _forbid("Filter parameters must be an object.", 400)
    invalid_keys = set(value) - VALID_SAVED_QUEUE_PARAMS
    if invalid_keys:
        raise _forbid(f"Invalid filter parameter keys: {', '.join(sorted(invalid_keys))}", 400)
    for k, v in value.items():
        if not isinstance(v, (str, int, float, bool)) and v is not None:
            raise _forbid(f"Filter parameter '{k}' has an invalid value type.", 400)
        if isinstance(v, str) and len(v) > 200:
            raise _forbid(f"Filter parameter '{k}' exceeds maximum length of 200 characters.", 400)
    return value


def _queue_out(q: SavedQueue, owner: Optional[User]) -> dict:
    return {"id": q.id, "name": q.name, "is_shared": q.is_shared, "params": q.params,
            "owner": {"id": q.user_id, "name": owner.display_name} if q.user_id and owner else None,
            "created_at": q.created_at.isoformat() if q.created_at else None,
            "updated_at": q.updated_at.isoformat() if q.updated_at else None}


def _owners(s, queues):
    ids = {q.user_id for q in queues if q.user_id}
    return {u.id: u for u in s.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _queue_scope(user):
    return (SavedQueue.tenant_id == user.tenant_id, or_(SavedQueue.user_id == user.user_id, SavedQueue.is_shared.is_(True)))


def _check_name(name: Optional[str]) -> str:
    n = (name or "").strip()
    if not n:
        raise _forbid("Name is required.", 400)
    if len(n) > 100:
        raise _forbid("Name is too long (max 100 characters).", 400)
    return n


@router.get("/api/saved-queues/")
async def list_saved_queues(user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        qs = s.scalars(select(SavedQueue).where(*_queue_scope(user)).order_by(SavedQueue.name)).all()
        owners = _owners(s, qs)
        return [_queue_out(q, owners.get(q.user_id)) for q in qs]

    return await run_in_tenant(user, _do, commit=False)


@router.post("/api/saved-queues/", status_code=status.HTTP_201_CREATED)
async def create_saved_queue(payload: SavedQueueIn, user: AuthenticatedUser = Depends(get_current_user)):
    """Managers and team leads may create shared queues; employees create personal queues."""
    def _do(s):
        name = _check_name(payload.name)
        params = _validate_params(payload.params if payload.params is not None else {})
        is_shared = bool(payload.is_shared) and user.role in PRIVILEGED_ROLES
        clash = s.scalars(select(SavedQueue.id).where(SavedQueue.tenant_id == user.tenant_id,
                                                      SavedQueue.user_id == user.user_id, SavedQueue.name == name)).first()
        if clash:
            raise _forbid("A saved queue with this name already exists.", 400)
        q = SavedQueue(tenant_id=user.tenant_id, user_id=user.user_id, name=name, is_shared=is_shared, params=params)
        s.add(q)
        s.flush()
        s.refresh(q)
        return _queue_out(q, _me(s, user))

    return await run_in_tenant(user, _do)


def _load_queue(s, user, id):
    q = s.scalars(select(SavedQueue).where(*_queue_scope(user), SavedQueue.id == id)).first()
    if not q:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return q


@router.get("/api/saved-queues/{id}/")
async def get_saved_queue(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        q = _load_queue(s, user, id)
        return _queue_out(q, _owners(s, [q]).get(q.user_id))

    return await run_in_tenant(user, _do, commit=False)


@router.patch("/api/saved-queues/{id}/")
async def update_saved_queue(payload: SavedQueueIn, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        q = _load_queue(s, user, id)
        if not q.is_shared and q.user_id and q.user_id != user.user_id and user.role not in TOP_ROLES:
            raise _forbid("You can only edit your own saved queues.")
        if q.is_shared and user.role not in PRIVILEGED_ROLES:
            raise _forbid("Only managers or team leads can edit shared queues.")
        if payload.is_shared is not None and payload.is_shared != q.is_shared and user.role not in PRIVILEGED_ROLES:
            raise _forbid("Only managers or team leads can change queue sharing state.")
        if payload.name is not None:
            q.name = _check_name(payload.name)
        if payload.params is not None:
            q.params = _validate_params(payload.params)
        if payload.is_shared is not None:
            q.is_shared = payload.is_shared
        s.flush()
        s.refresh(q)
        return _queue_out(q, _owners(s, [q]).get(q.user_id))

    return await run_in_tenant(user, _do)


@router.delete("/api/saved-queues/{id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_queue(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    def _do(s):
        q = _load_queue(s, user, id)
        if not q.is_shared and q.user_id and q.user_id != user.user_id and user.role not in TOP_ROLES:
            raise _forbid("You can only delete your own saved queues.")
        if q.is_shared and user.role not in PRIVILEGED_ROLES:
            raise _forbid("Only managers or team leads can delete shared queues.")
        s.delete(q)

    await run_in_tenant(user, _do)
    return None
