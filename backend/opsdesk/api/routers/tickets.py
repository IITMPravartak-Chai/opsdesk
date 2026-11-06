"""Tickets, interactions, attachments and canned responses.

Reads use the raw-SQL repository (fast, tenant-scoped via the signed context); writes go through the shared
service layer so SLA, history, audit, automation, AI enrichment and notifications behave identically for every
entry point.
"""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import joinedload

from ...db.base import utcnow
from ...db.models import (Account, Approval, Attachment, CannedResponse, Category, ConfigurationItem, CustomField,
                          Interaction, Priority, Service, State, Team, Ticket, TicketLink, TicketType, TicketWatcher,
                          TimeEntry, Transition, User)
from ...db.models.core import PRIVILEGED_ROLES, STAFF_ROLES
from ...services import commit_hooks, tickets as services
from ...services.attachments import AttachmentRejected, store_attachment
from ...services.audit import audit
from ...services.visibility import visible_ticket_criteria
from ...services.workflow import TransitionError
from ...storage import get_storage
from ...settings import settings
from ..multipart_helper import MAX_UPLOAD_SIZE, parse_request_form, read_bounded_body
from ..repositories.tickets import ticket_repo
from ..runtime import run_in_tenant
from ..schemas.tickets import (CannedResponseItem, EventResponse, InteractionResponse, MAJOR_STATUSES,
                               MajorCoordinateRequest, MajorDesignateRequest, PaginatedTicketResponse,
                               TicketCountsResponse, TicketCreateRequest, TicketDetailResponse,
                               TicketTransitionRequest, TicketUpdateRequest)
from ..security import AuthenticatedUser, get_authenticated_user, get_current_user
from ..tenant_context import tenant_transaction

router = APIRouter(tags=["tickets"])

LINK_TYPES = ("related", "duplicate_of", "caused_by", "blocks")
MAJOR_DENIED = "Only team leads, managers and the super admin can declare or coordinate a major incident."


def _bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _not_found(detail: str = "Not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _build_pagination_url(request: Request, target_page: int) -> str:
    """Absolute pagination URL in the paginated list format the web client expects."""
    split = urlsplit(str(request.url))
    query_params = parse_qs(split.query)
    query_params["page"] = [str(target_page)]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query_params, doseq=True), split.fragment))


def actor_row(s, user: AuthenticatedUser) -> User:
    u = s.scalars(select(User).options(joinedload(User.tenant)).where(
        User.id == user.user_id, User.tenant_id == user.tenant_id)).first()
    if not u:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    return u


def visible_ticket(s, actor: User, ticket_id: int, *, options=()) -> Ticket | None:
    return s.scalars(select(Ticket).options(*options).where(Ticket.id == ticket_id, visible_ticket_criteria(s, actor))).first()


def _customer_editable_fields(s, values, ticket_type_id):
    """A requester may only set custom fields the ticket type shows to requesters."""
    allowed = set(s.scalars(select(CustomField.key).where(CustomField.ticket_type_id == ticket_type_id,
                                                          CustomField.visible_to_customer.is_(True))))
    return {k: v for k, v in (values or {}).items() if k in allowed}


# Related fields a staff update may set: model, and whether null (clear) is allowed.
_UPDATE_REFS = {
    "account": (Account, True), "category": (Category, True), "service": (Service, True),
    "ci": (ConfigurationItem, True), "team": (Team, True), "assignee": (User, True),
    "priority": (Priority, False), "parent": (Ticket, True),
}


def _resolve_update_refs(s, data, ticket, tenant_id):
    """Turn the ids in a staff update into instances, in place. Each id must belong to the caller's tenant; the
    assignee must be active staff; priority cannot be cleared; a ticket cannot be its own parent; impact and
    urgency are 1-4. Invalid input is a 400."""
    def invalid(field):
        raise _bad(f"Invalid {field}.")

    for field, (model, nullable) in _UPDATE_REFS.items():
        if field not in data:
            continue
        pk = data[field]
        if pk is None:
            if not nullable:
                invalid(field)
            continue
        stmt = select(model).where(model.id == pk, model.tenant_id == tenant_id)
        if field == "assignee":
            stmt = stmt.where(User.role.in_(STAFF_ROLES), User.is_active.is_(True))
        obj = s.scalars(stmt).first()
        if obj is None or (field == "parent" and obj.id == ticket.id):
            invalid(field)
        data[field] = obj
    for field in ("impact", "urgency"):
        if field in data and (data[field] is None or not 1 <= data[field] <= 4):
            invalid(field)


def _resolve_create_refs(s, payload, actor: User, tenant_id):
    """The create payload carries ids; the service needs instances. Each id must belong to the caller's tenant, and a
    requester may only use what GET /api/meta/ offers them: their own account, shared or own-account services,
    and own-account CIs."""
    def one(model, pk, name, *conds):
        if not pk:
            return None
        obj = s.scalars(select(model).where(model.id == pk, model.tenant_id == tenant_id, *conds)).first()
        if obj is None:
            raise _bad(f"Invalid {name}.")
        return obj

    refs = {"category": one(Category, payload.category, "category")}
    if actor.is_agent:
        refs.update(account=one(Account, payload.account, "account"), service=one(Service, payload.service, "service"),
                    ci=one(ConfigurationItem, payload.ci, "ci"))
        return refs
    if payload.account and payload.account != actor.account_id:
        raise _bad("Invalid account.")
    from sqlalchemy import or_
    refs["service"] = one(Service, payload.service, "service", Service.is_active.is_(True),
                          or_(Service.account_id == actor.account_id, Service.account_id.is_(None)))
    refs["ci"] = one(ConfigurationItem, payload.ci, "ci", ConfigurationItem.status != "retired",
                     ConfigurationItem.account_id == actor.account_id)
    return refs


# -----------------------------------------------------------------------------
# 1. List & counts
# -----------------------------------------------------------------------------

@router.get("/api/tickets/", response_model=PaginatedTicketResponse)
async def list_tickets(
    request: Request,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
    ordering: str = Query("updated", description="Sort order: updated, created, due, priority, oldest"),
    q: Optional[str] = Query(None), state_category: Optional[str] = Query(None), state: Optional[str] = Query(None),
    priority: Optional[str] = Query(None), assignee: Optional[str] = Query(None), team: Optional[str] = Query(None),
    account: Optional[str] = Query(None), type: Optional[str] = Query(None), sla_status: Optional[str] = Query(None),
    source: Optional[str] = Query(None), major: Optional[str] = Query(None), mine: Optional[str] = Query(None),
    open: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(get_authenticated_user),
):
    """List tickets for the current tenant, filtered by visibility and query parameters."""
    offset = (page - 1) * page_size
    filters = {"q": q, "state_category": state_category, "state": state, "priority": priority, "assignee": assignee,
               "team": team, "account": account, "type": type, "sla_status": sla_status, "source": source,
               "major": major, "mine": mine, "open": open}
    async with tenant_transaction(user) as conn:
        items, total_count = await ticket_repo.list_tickets(conn=conn, user=user, filters=filters, limit=page_size,
                                                            offset=offset, ordering=ordering)
    if total_count > 0 and offset >= total_count:
        raise _not_found("Invalid page.")
    if total_count == 0 and page > 1:
        raise _not_found("Invalid page.")
    return PaginatedTicketResponse(
        count=total_count,
        next=_build_pagination_url(request, page + 1) if (offset + page_size) < total_count else None,
        previous=_build_pagination_url(request, page - 1) if page > 1 else None, results=items)


@router.get("/api/tickets/counts/", response_model=TicketCountsResponse)
async def get_ticket_counts(user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Badge counts for standard queues in navigation."""
    async with tenant_transaction(user) as conn:
        return await ticket_repo.get_counts(conn, user)


# -----------------------------------------------------------------------------
# 2. Bulk actions
# -----------------------------------------------------------------------------

@router.post("/api/tickets/bulk/")
async def bulk_tickets(request: Request, payload: dict, user: AuthenticatedUser = Depends(get_current_user)):
    """Safe batch operations across multiple tickets, each in its own savepoint.

    assign {assignee_id?, team_id?} | transition {transition_id, comment?} | set_priority {priority_key} |
    add_tag {tag} | remove_tag {tag}. 207 with per-ticket results when some fail."""
    action_name = payload.get("action")
    ticket_ids = payload.get("ticket_ids")
    if not action_name or not isinstance(ticket_ids, list):
        raise _bad("action and ticket_ids (list) are required.")
    if len(ticket_ids) > 100:
        raise _bad("Cannot process more than 100 tickets in a single batch.")

    def _do(s):
        actor = actor_row(s, user)
        valid_ids = [i for i in ticket_ids if isinstance(i, int) and not isinstance(i, bool)]
        ticket_map = {t.id: t for t in s.scalars(select(Ticket).where(Ticket.id.in_(valid_ids),
                                                                       visible_ticket_criteria(s, actor)))}
        results, ok, bad = {}, 0, 0
        for tid in ticket_ids:
            if tid not in ticket_map:
                results[str(tid)] = {"success": False, "error": "Ticket not found or access denied."}
                bad += 1
                continue
            t = ticket_map[tid]
            try:
                with commit_hooks.savepoint(s):
                    results[str(tid)] = _bulk_one(s, actor, t, action_name, payload, request)
                ok += 1
            except Exception as e:  # noqa: BLE001 - per-ticket failure is reported, not raised
                results[str(tid)] = {"success": False, "error": str(e)}
                bad += 1
        return {"action": action_name, "total": len(ticket_ids), "succeeded": ok, "failed": bad, "results": results}, bad

    body, failures = await run_in_tenant(user, _do)
    return JSONResponse(content=body, status_code=status.HTTP_200_OK if failures == 0 else status.HTTP_207_MULTI_STATUS)


def _bulk_one(s, actor: User, t: Ticket, action_name: str, data: dict, request: Request) -> dict:
    tenant_id = actor.tenant_id
    if action_name == "assign":
        assignee_id, team_id = data.get("assignee_id"), data.get("team_id")
        if assignee_id is None and team_id is None:
            raise ValueError("Either assignee_id or team_id must be provided.")
        update_data, assignee, team = {}, None, None
        if assignee_id is not None:
            if assignee_id:
                assignee = s.scalars(select(User).where(User.id == assignee_id, User.tenant_id == tenant_id,
                                                        User.is_active.is_(True))).first()
                if not assignee or not assignee.is_agent:
                    raise ValueError("Assignee must be an active staff member in this organization.")
            update_data["assignee"] = assignee
        if team_id is not None:
            if team_id:
                team = s.scalars(select(Team).where(Team.id == team_id, Team.tenant_id == tenant_id)).first()
                if not team:
                    raise ValueError("Team not found.")
            update_data["team"] = team
        # Same rule as assigning a single ticket: any active staff member may be asked to take it, whatever the ticket's
        # team (a team routes work; it does not limit who may help). Only an explicit team + member pair must agree.
        if team is not None and assignee is not None and team.members and assignee.id not in {m.id for m in team.members}:
            raise ValueError(f"Assignee '{assignee.display_name}' is not a member of team '{team.name}'.")
        if update_data:
            services.update_ticket(s, t, actor, update_data, request=request)
        return {"success": True, "ticket_number": t.number}
    if action_name == "transition":
        transition_id, comment = data.get("transition_id"), data.get("comment", "")
        tr = s.scalars(select(Transition).where(Transition.id == transition_id,
                                                Transition.workflow_id == t.state.workflow_id)).first()
        if not tr:
            raise ValueError(f"Transition {transition_id} is not valid for state '{t.state.name}'.")
        old_state_name = t.state.name
        services.perform_transition(s, t, tr, actor, comment=comment)
        audit(s, t.tenant_id, "ticket.transition", t, actor, {"state": [old_state_name, t.state.name]}, request)
        return {"success": True, "ticket_number": t.number, "new_state": t.state.name}
    if action_name == "set_priority":
        key = data.get("priority_key")
        p = s.scalars(select(Priority).where(Priority.tenant_id == tenant_id, Priority.key == key)).first()
        if not p:
            raise ValueError(f"Priority '{key}' not found.")
        services.update_ticket(s, t, actor, {"priority": p}, request=request)
        return {"success": True, "ticket_number": t.number, "priority": p.key}
    if action_name in ("add_tag", "remove_tag"):
        tag = (data.get("tag") or "").strip().lower()
        if not tag:
            raise ValueError("Tag cannot be empty.")
        current = list(t.tags or [])
        if action_name == "add_tag":
            if len(tag) > 50:
                raise ValueError("Tag exceeds maximum length of 50 characters.")
            if tag not in current:
                new = current + [tag]
                t.tags = new
                s.flush()
                services.log_event(s, t, "tag_added", actor, tag=tag)
                audit(s, t.tenant_id, "ticket.update", t, actor, {"tags": [current, new]}, request)
        elif tag in current:
            new = [x for x in current if x != tag]
            t.tags = new
            s.flush()
            services.log_event(s, t, "tag_removed", actor, tag=tag)
            audit(s, t.tenant_id, "ticket.update", t, actor, {"tags": [current, new]}, request)
        return {"success": True, "ticket_number": t.number}
    raise ValueError(f"Unsupported bulk action '{action_name}'.")


# -----------------------------------------------------------------------------
# 3. Detail, create, update
# -----------------------------------------------------------------------------

@router.get("/api/tickets/{id}/", response_model=TicketDetailResponse)
async def get_ticket_detail(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Complete ticket detail including SLA, transitions, approvals, and links."""
    async with tenant_transaction(user) as conn:
        detail = await ticket_repo.get_ticket_detail(conn, id, user)
    if not detail:
        raise _not_found()
    return detail


@router.post("/api/tickets/", response_model=TicketDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(payload: TicketCreateRequest, user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Create a new ticket, applying priority matrix, initial state and SLA rules."""
    def _create(s):
        actor = actor_row(s, user)
        ttype = s.scalars(select(TicketType).where(TicketType.id == payload.ticket_type,
                                                   TicketType.tenant_id == user.tenant_id,
                                                   TicketType.is_active.is_(True))).first()
        if not ttype:
            raise _bad("Invalid ticket type.")
        if not actor.is_agent and not ttype.customer_can_create:
            raise _bad("Customers cannot create this ticket type.", status.HTTP_403_FORBIDDEN)

        requester = None
        if payload.requester:
            if actor.is_agent:
                requester = s.scalars(select(User).where(User.id == payload.requester,
                                                         User.tenant_id == user.tenant_id)).first()
            else:
                requester = actor
        elif not actor.is_agent:
            requester = actor

        refs = _resolve_create_refs(s, payload, actor, user.tenant_id)
        custom_fields = payload.custom_fields
        if not actor.is_agent:
            custom_fields = _customer_editable_fields(s, custom_fields, ttype.id)
        created = services.create_ticket(
            s, tenant_id=user.tenant_id, ticket_type=ttype, subject=payload.subject, description=payload.description,
            requester=requester, created_by=actor, impact=payload.impact, urgency=payload.urgency,
            custom_fields=custom_fields, tags=payload.tags,
            is_major_incident=payload.is_major_incident if actor.role in PRIVILEGED_ROLES else False,  # leads decide
            **refs)
        return created.id

    created_id = await run_in_tenant(user, _create)
    async with tenant_transaction(user) as conn:
        detail = await ticket_repo.get_ticket_detail(conn, created_id, user)
    if not detail:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ticket created but failed to load.")
    return detail


@router.patch("/api/tickets/{id}/", response_model=TicketDetailResponse)
async def update_ticket(payload: TicketUpdateRequest, request: Request, id: int = Path(..., ge=1),
                        user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Partially update a ticket, auditing changes and re-evaluating SLA if needed."""
    def _update(s):
        actor = actor_row(s, user)
        ticket = visible_ticket(s, actor, id)
        if not ticket:
            raise _not_found()
        update_data = payload.model_dump(exclude_unset=True)
        if "is_major_incident" in update_data and actor.role not in PRIVILEGED_ROLES:
            raise _bad(MAJOR_DENIED, status.HTTP_403_FORBIDDEN)
        if actor.is_agent:
            _resolve_update_refs(s, update_data, ticket, user.tenant_id)
        else:
            # Requesters may only edit CUSTOMER_EDITABLE fields; drop the rest unresolved, so a requester cannot
            # probe which ids exist.
            update_data = {k: v for k, v in update_data.items() if k in services.CUSTOMER_EDITABLE}
            if "custom_fields" in update_data:
                update_data["custom_fields"] = _customer_editable_fields(s, update_data["custom_fields"], ticket.ticket_type_id)
        services.update_ticket(s, ticket, actor, update_data, request=request)

    await run_in_tenant(user, _update)
    async with tenant_transaction(user) as conn:
        detail = await ticket_repo.get_ticket_detail(conn, id, user)
    if not detail:
        raise _not_found()
    return detail


# -----------------------------------------------------------------------------
# 4. Workflow transitions
# -----------------------------------------------------------------------------

@router.post("/api/tickets/{id}/transition/", response_model=TicketDetailResponse)
async def transition_ticket(payload: TicketTransitionRequest, id: int = Path(..., ge=1),
                            user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Execute a valid workflow transition on a ticket."""
    def _transition(s):
        actor = actor_row(s, user)
        ticket = visible_ticket(s, actor, id)
        if not ticket:
            raise _not_found()
        transition = s.scalars(select(Transition).where(Transition.id == payload.transition_id,
                                                        Transition.workflow_id == ticket.state.workflow_id)).first()
        if not transition:
            raise _not_found("Transition not found.")
        fields = payload.fields or {}
        if not actor.is_agent:
            # perform_transition writes resolution fields and any custom-field key it is given.
            fields = _customer_editable_fields(s, fields, ticket.ticket_type_id)
        try:
            services.perform_transition(s, ticket, transition, actor, comment=payload.comment or "", fields=fields,
                                        comment_public=payload.comment_public)
        except TransitionError as exc:
            raise _bad(str(exc))

    await run_in_tenant(user, _transition)
    async with tenant_transaction(user) as conn:
        return await ticket_repo.get_ticket_detail(conn, id, user)


# -----------------------------------------------------------------------------
# 5. Interactions & attachments
# -----------------------------------------------------------------------------

@router.get("/api/tickets/{id}/interactions/", response_model=List[InteractionResponse])
@router.get("/api/tickets/{id}/comments/", response_model=List[InteractionResponse])
async def list_interactions(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    async with tenant_transaction(user) as conn:
        return await ticket_repo.list_interactions(conn, id, user)


@router.post("/api/tickets/{id}/interactions/", response_model=InteractionResponse, status_code=status.HTTP_201_CREATED)
@router.post("/api/tickets/{id}/comments/", response_model=InteractionResponse, status_code=status.HTTP_201_CREATED)
async def add_interaction(request: Request, id: int = Path(..., ge=1),
                          user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Post a comment, public reply, or internal note to a ticket."""
    raw_body = await read_bounded_body(request, MAX_UPLOAD_SIZE)
    fields, files = parse_request_form(raw_body, request.headers.get("content-type", ""))
    cleaned_body = (fields.get("body") or "").strip()
    if not cleaned_body and not files:
        raise _bad("Write a message or attach a file.")
    is_mobile = request.headers.get("X-Client") == "mobile"
    kind_val = fields.get("kind", "public")

    def _add(s):
        actor = actor_row(s, user)
        ticket = visible_ticket(s, actor, id)
        if not ticket:
            raise _not_found()
        if ticket.state.is_final:
            raise _bad("This ticket is closed. Raise a new one and reference it.")
        effective_kind = kind_val if (actor.is_agent and kind_val in ("public", "internal")) else "public"
        it = services.add_interaction(s, ticket, body=cleaned_body or "(attachment)", author=actor,
                                      kind=effective_kind, source="mobile" if is_mobile else "portal")
        errors = []
        for f in files:
            try:
                store_attachment(s, ticket, f.filename, f.content, f.content_type, it, actor)
            except AttachmentRejected as exc:
                errors.append(f"{f.filename}: {exc}")
        return it.id, errors

    it_id, errors = await run_in_tenant(user, _add)
    async with tenant_transaction(user) as conn:
        all_it = await ticket_repo.list_interactions(conn, id, user)
    matching = [item for item in all_it if item.id == it_id]
    if not matching:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load interaction.")
    res = matching[0]
    res.attachment_errors = errors if errors else None
    return res


@router.post("/api/tickets/{id}/attachments/", status_code=status.HTTP_201_CREATED)
async def upload_ticket_attachments(request: Request, id: int = Path(..., ge=1),
                                    user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Upload attachments directly to a ticket."""
    raw_body = await read_bounded_body(request, MAX_UPLOAD_SIZE)
    _, files = parse_request_form(raw_body, request.headers.get("content-type", ""))

    def _upload(s):
        actor = actor_row(s, user)
        ticket = visible_ticket(s, actor, id)
        if not ticket:
            raise _not_found()
        out, errors = [], []
        for f in files:
            try:
                out.append(store_attachment(s, ticket, f.filename, f.content, f.content_type, None, actor).id)
            except AttachmentRejected as exc:
                errors.append(f"{f.filename}: {exc}")
        if not out:
            raise _bad("No valid files uploaded.")
        return out, errors

    out, errors = await run_in_tenant(user, _upload)
    return {"created": out, "errors": errors}


@router.get("/api/attachments/{id}/download/")
async def download_attachment(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Attachment download enforcing tenant and visibility checks. S3 storage redirects to a short-lived presigned
    URL; local storage streams the file."""
    def _lookup(s):
        actor = actor_row(s, user)
        att = s.scalars(select(Attachment).options(joinedload(Attachment.interaction)).where(
            Attachment.id == id, Attachment.tenant_id == user.tenant_id)).first()
        if not att:
            raise _not_found()
        if not s.scalars(select(Ticket.id).where(Ticket.id == att.ticket_id, visible_ticket_criteria(s, actor))).first():
            raise _not_found()
        if att.interaction and att.interaction.kind == "internal" and not actor.is_agent:
            raise _not_found()
        if att.scan_status in ("infected", "pending"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file is awaiting or failed malware scanning.")
        return att.file, att.filename, att.content_type

    key, filename, media_type = await run_in_tenant(user, _lookup, commit=False)
    storage = get_storage()
    url = storage.download_url(key, filename, media_type, settings.s3_presign_seconds)
    if url:
        return Response(status_code=status.HTTP_302_FOUND, headers={"Location": url})
    from starlette.concurrency import run_in_threadpool
    data = await run_in_threadpool(storage.read, key)
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return Response(content=data, media_type=media_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


# -----------------------------------------------------------------------------
# 6. Watch, link, merge, approvals, time, events
# -----------------------------------------------------------------------------

@router.post("/api/tickets/{id}/watch/")
async def toggle_watch(id: int = Path(..., ge=1), payload: Optional[dict] = None,
                       user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Set watcher status on a ticket the caller can already see (for requesters watching grants visibility, so a
    tenant-wide lookup would let them subscribe to, and then read, any ticket)."""
    watch_flag = (payload or {}).get("watch", True)

    def _toggle(s):
        actor = actor_row(s, user)
        ticket = visible_ticket(s, actor, id)
        if not ticket:
            raise _not_found()
        existing = s.scalars(select(TicketWatcher).where(TicketWatcher.ticket_id == ticket.id,
                                                         TicketWatcher.user_id == actor.id)).first()
        if watch_flag is False:
            if existing:
                s.delete(existing)
        elif not existing:
            s.add(TicketWatcher(ticket_id=ticket.id, user_id=actor.id))
        s.flush()
        return s.scalars(select(TicketWatcher.id).where(TicketWatcher.ticket_id == ticket.id,
                                                        TicketWatcher.user_id == actor.id)).first() is not None

    return {"watching": await run_in_tenant(user, _toggle)}


@router.post("/api/tickets/{id}/link/")
async def link_tickets(payload: dict, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Link two tickets together (staff role required)."""
    if not user.is_agent:
        raise _bad("Only staff can link tickets.", status.HTTP_403_FORBIDDEN)

    def _link(s):
        actor = actor_row(s, user)
        source = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        target = s.scalars(select(Ticket).where(func.lower(Ticket.number) == str(payload.get("number", "")).lower(),
                                                Ticket.tenant_id == user.tenant_id)).first()
        link_type = payload.get("link_type", "related")
        if not source or not target or source.id == target.id or link_type not in LINK_TYPES:
            raise _bad("Choose a different ticket or link type.")
        exists = s.scalars(select(TicketLink.id).where(TicketLink.source_id == source.id, TicketLink.target_id == target.id,
                                                       TicketLink.link_type == link_type)).first()
        if exists is None:
            s.add(TicketLink(tenant_id=user.tenant_id, source_id=source.id, target_id=target.id, link_type=link_type,
                             created_by_id=actor.id))
        services.log_event(s, source, "linked", actor, target=target.number, link_type=link_type)

    await run_in_tenant(user, _link)
    return {"ok": True}


@router.post("/api/tickets/{id}/merge/")
async def merge_tickets(payload: dict, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Merge a duplicate ticket into a primary ticket."""
    if not user.is_agent:
        raise _bad("Only staff can merge tickets.", status.HTTP_403_FORBIDDEN)

    def _merge(s):
        actor = actor_row(s, user)
        dup = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        primary = s.scalars(select(Ticket).where(func.lower(Ticket.number) == str(payload.get("into", "")).lower(),
                                                 Ticket.tenant_id == user.tenant_id)).first()
        if not dup or not primary or dup.id == primary.id:
            raise _bad("Can't merge a ticket into itself.")
        services.merge_into(s, dup, primary, actor)
        return primary.id

    return {"merged_into": await run_in_tenant(user, _merge)}


@router.post("/api/tickets/{id}/approvals/", status_code=status.HTTP_201_CREATED)
async def request_approval(payload: dict, id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Request approval from a designated user."""
    if not user.is_agent:
        raise _bad("Only staff can request approvals.", status.HTTP_403_FORBIDDEN)

    def _req(s):
        actor = actor_row(s, user)
        ticket = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        approver = s.scalars(select(User).where(User.id == payload.get("approver_id"),
                                                User.tenant_id == user.tenant_id)).first()
        if not ticket or not approver:
            raise _not_found("Ticket or approver not found.")
        a = s.scalars(select(Approval).where(Approval.ticket_id == ticket.id, Approval.approver_id == approver.id,
                                             Approval.status == "pending")).first()
        if a is None:
            a = Approval(tenant_id=user.tenant_id, ticket_id=ticket.id, approver_id=approver.id,
                         requested_by_id=actor.id, status="pending")
            s.add(a)
            s.flush()
        services.log_event(s, ticket, "approval_requested", actor, approver=approver.display_name)
        return a.id

    return {"id": await run_in_tenant(user, _req)}


@router.post("/api/tickets/{id}/approvals/{approval_id}/decide/")
async def decide_approval(payload: dict, id: int = Path(..., ge=1), approval_id: int = Path(..., ge=1),
                          user: AuthenticatedUser = Depends(get_current_user)):
    """Submit an approval decision (approve or reject)."""
    def _decide(s):
        actor = actor_row(s, user)
        ticket = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        approval = (s.scalars(select(Approval).where(Approval.id == approval_id, Approval.ticket_id == ticket.id)).first()
                    if ticket else None)
        if not ticket or not approval:
            raise _not_found("Approval not found.")
        if approval.approver_id != user.user_id:
            raise _bad("Only the named approver can decide.", status.HTTP_403_FORBIDDEN)
        decision = payload.get("decision")
        if decision not in ("approved", "rejected"):
            raise _bad("Invalid decision.")
        approval.status = decision
        approval.comment = payload.get("comment", "")
        approval.decided_at = utcnow()
        s.flush()
        services.log_event(s, ticket, f"approval_{approval.status}", actor, comment=approval.comment)
        return approval.status

    return {"status": await run_in_tenant(user, _decide)}


@router.get("/api/tickets/{id}/time/")
@router.post("/api/tickets/{id}/time/")
async def ticket_time(request: Request, id: int = Path(..., ge=1), payload: Optional[dict] = None,
                      user: AuthenticatedUser = Depends(get_current_user)):
    """Log or retrieve time entries for a ticket (staff role required)."""
    if not user.is_agent:
        raise _bad("Only staff can track time.", status.HTTP_403_FORBIDDEN)
    method = request.method

    def _time(s):
        actor = actor_row(s, user)
        ticket = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        if not ticket:
            raise _not_found()
        if method == "POST":
            minutes = int((payload or {}).get("minutes") or 0)
            if minutes <= 0:
                raise _bad("Enter the minutes spent.")
            wd = (payload or {}).get("work_date")
            try:
                work_date = date.fromisoformat(wd) if wd else date.today()
            except ValueError:
                raise _bad("Invalid work_date.")
            s.add(TimeEntry(tenant_id=user.tenant_id, ticket_id=ticket.id, user_id=actor.id, minutes=minutes,
                            billable=bool((payload or {}).get("billable", True)),
                            note=(payload or {}).get("note", "")[:300], work_date=work_date))
            s.flush()
        entries = s.scalars(select(TimeEntry).where(TimeEntry.ticket_id == ticket.id).order_by(TimeEntry.id.desc())).all()
        users = {u.id: u for u in s.scalars(select(User).where(User.id.in_({e.user_id for e in entries})))} if entries else {}
        return [{"id": e.id, "user": {"id": e.user_id, "name": users[e.user_id].display_name}, "minutes": e.minutes,
                 "billable": e.billable, "note": e.note, "work_date": str(e.work_date)} for e in entries]

    return await run_in_tenant(user, _time, commit=method == "POST")


@router.get("/api/tickets/{id}/events/", response_model=List[EventResponse])
async def list_ticket_events(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Event trail for a ticket (staff role required)."""
    if not user.is_agent:
        raise _bad("Only staff can view audit events.", status.HTTP_403_FORBIDDEN)
    async with tenant_transaction(user) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ev.id, ev.event_type, ev.data, ev.created_at,
                       u.id AS actor_id, COALESCE(NULLIF(TRIM(CONCAT(u.first_name, ' ', u.last_name)), ''), u.email) AS actor_name
                FROM tickets_ticketevent ev
                LEFT JOIN core_user u ON u.id = ev.actor_id
                WHERE ev.ticket_id = %s AND ev.tenant_id = %s
                ORDER BY ev.id ASC
                """, (id, user.tenant_id))
            rows = await cur.fetchall()
            return [EventResponse(id=r[0], event_type=r[1], data=r[2] if isinstance(r[2], dict) else {},
                                  created_at=r[3].isoformat() if r[3] else "",
                                  actor={"id": r[4], "name": r[5]} if r[4] else None) for r in rows]


# -----------------------------------------------------------------------------
# 7. Canned responses
# -----------------------------------------------------------------------------

def _staff_only(user: AuthenticatedUser, what: str):
    if not user.is_agent:
        raise _bad(f"Only staff can {what}.", status.HTTP_403_FORBIDDEN)


@router.get("/api/canned-responses/", response_model=List[CannedResponseItem])
async def list_canned_responses(user: AuthenticatedUser = Depends(get_current_user)):
    _staff_only(user, "access canned responses")
    async with tenant_transaction(user) as conn:
        return await ticket_repo.list_canned_responses(conn, user)


@router.get("/api/canned-responses/{id}/", response_model=CannedResponseItem)
async def get_canned_response(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    _staff_only(user, "access canned responses")
    async with tenant_transaction(user) as conn:
        items = await ticket_repo.list_canned_responses(conn, user)
    matching = [item for item in items if item.id == id]
    if not matching:
        raise _not_found("Canned response not found.")
    return matching[0]


@router.get("/api/canned-responses/{id}/render/")
async def render_canned_response(id: int = Path(..., ge=1), ticket: int = Query(..., ge=1),
                                 user: AuthenticatedUser = Depends(get_current_user)):
    """Render a canned response with ticket and agent context variables."""
    _staff_only(user, "render canned responses")
    async with tenant_transaction(user) as conn:
        rendered = await ticket_repo.render_canned_response(conn, id, ticket, user)
    if rendered is None:
        raise _not_found("Canned response or ticket not found.")
    return {"body": rendered}


# -----------------------------------------------------------------------------
# 8. Major incidents
# -----------------------------------------------------------------------------

def _commander(s, pk, tenant_id) -> User:
    c = s.scalars(select(User).where(User.id == pk, User.tenant_id == tenant_id, User.role.in_(STAFF_ROLES),
                                     User.is_active.is_(True))).first()
    if c is None:
        raise _bad("Incident commander must be active staff.")
    return c


@router.post("/api/tickets/{id}/designate_major/", response_model=TicketDetailResponse)
async def designate_major(payload: MajorDesignateRequest, id: int = Path(..., ge=1),
                          user: AuthenticatedUser = Depends(get_current_user)):
    """Declare a ticket a major incident (with commander and initial status), or demote it."""
    if user.role not in PRIVILEGED_ROLES:
        raise _bad(MAJOR_DENIED, status.HTTP_403_FORBIDDEN)

    def _designate(s):
        actor = actor_row(s, user)
        t = visible_ticket(s, actor, id, options=(joinedload(Ticket.incident_commander),))
        if not t:
            raise _not_found()
        commander = None
        if payload.commander_id:
            commander = _commander(s, payload.commander_id, user.tenant_id)
        elif payload.is_major_incident and not t.incident_commander_id:
            commander = actor
        if payload.status is not None and payload.status not in MAJOR_STATUSES:
            raise _bad(f"Invalid major incident status '{payload.status}'.")

        old_major = t.is_major_incident
        t.is_major_incident = payload.is_major_incident
        if payload.is_major_incident:
            if commander:
                t.incident_commander = commander
            if not t.major_incident_status:
                t.major_incident_status = payload.status or "investigating"
            s.flush()
            services.log_event(s, t, "major_incident_designated", actor,
                               commander=t.incident_commander.display_name if t.incident_commander else None,
                               status=t.major_incident_status)
            audit(s, t.tenant_id, "ticket.major_designated", t, actor,
                  {"is_major_incident": [old_major, True], "commander": t.incident_commander_id})
            s.add(Interaction(
                tenant_id=t.tenant_id, ticket_id=t.id, author_id=actor.id, kind="internal", direction="internal",
                body=(f"*** Major Incident Declared ***\nDesignated by: {actor.display_name}\n"
                      f"Incident Commander: {t.incident_commander.display_name if t.incident_commander else 'Unassigned'}\n"
                      f"Status: {t.major_incident_status}"),
                source="portal", delivery_status="n/a", external_message_id="", delivery_error="", created_at=utcnow()))
        else:
            t.major_incident_status = ""
            services.log_event(s, t, "major_incident_demoted", actor)
            audit(s, t.tenant_id, "ticket.major_demoted", t, actor, {"is_major_incident": [old_major, False]})
        s.flush()

    await run_in_tenant(user, _designate)
    async with tenant_transaction(user) as conn:
        return await ticket_repo.get_ticket_detail(conn, id, user)


@router.post("/api/tickets/{id}/major_coordinate/", response_model=TicketDetailResponse)
async def major_coordinate(payload: MajorCoordinateRequest, id: int = Path(..., ge=1),
                           user: AuthenticatedUser = Depends(get_current_user)):
    """Coordinate a major incident: status, commander, post-incident review, child incidents, note. All changes are
    applied in one transaction: a rejected part leaves nothing changed."""
    if user.role not in PRIVILEGED_ROLES:
        raise _bad(MAJOR_DENIED, status.HTTP_403_FORBIDDEN)
    sent = payload.model_fields_set

    def _coordinate(s):
        actor = actor_row(s, user)
        t = visible_ticket(s, actor, id, options=(joinedload(Ticket.parent),))
        if not t:
            raise _not_found()
        if not t.is_major_incident:
            raise _bad("This action is only valid on major incidents.")

        changes, touched = {}, False
        if payload.status and payload.status != t.major_incident_status:
            if payload.status not in MAJOR_STATUSES:
                raise _bad(f"Invalid major incident status '{payload.status}'.")
            changes["status"] = [t.major_incident_status, payload.status]
            services.log_event(s, t, "major_status_changed", actor, old_status=t.major_incident_status,
                               new_status=payload.status)
            t.major_incident_status = payload.status
            touched = True
        if "commander_id" in sent:
            if payload.commander_id:
                new_c = _commander(s, payload.commander_id, user.tenant_id)
                if t.incident_commander_id != new_c.id:
                    changes["commander"] = [t.incident_commander_id, new_c.id]
                    t.incident_commander = new_c
                    touched = True
                    services.log_event(s, t, "commander_reassigned", actor, commander=new_c.display_name)
            elif t.incident_commander_id:
                changes["commander"] = [t.incident_commander_id, None]
                t.incident_commander = None
                touched = True
                services.log_event(s, t, "commander_unassigned", actor)
        if "post_incident_review" in sent and payload.post_incident_review is not None:
            review = payload.post_incident_review.strip()
            if review != (t.post_incident_review or ""):
                t.post_incident_review = review
                touched = True
                changes["post_incident_review"] = True

        children = []
        if payload.link_ticket_ids:
            ids = set(payload.link_ticket_ids) - {t.id}
            children = list(s.scalars(select(Ticket).where(Ticket.id.in_(ids), visible_ticket_criteria(s, actor))))
            ancestors, curr = set(), t.parent
            while curr:
                ancestors.add(curr.id)
                curr = curr.parent
            if len(children) != len(ids) or any(c.id in ancestors for c in children):
                raise _bad("Invalid link_ticket_ids.")

        if touched:
            s.flush()
            audit(s, t.tenant_id, "ticket.major_coordinated", t, actor, changes)
        for child in children:
            child.parent = t
            s.flush()
            services.log_event(s, child, "associated_with_major_incident", actor, major_number=t.number)
            services.log_event(s, t, "child_incident_associated", actor, child_number=child.number)
        note = (payload.note or "").strip()
        if note:
            s.add(Interaction(
                tenant_id=t.tenant_id, ticket_id=t.id, author_id=actor.id, kind="internal", direction="internal",
                body=f"[Major Incident Update - {(t.major_incident_status or 'active').upper()}]\n{note}",
                source="portal", delivery_status="n/a", external_message_id="", delivery_error="", created_at=utcnow()))
            services.log_event(s, t, "major_coordination_note", actor, note_preview=note[:100])
        s.flush()

    await run_in_tenant(user, _coordinate)
    async with tenant_transaction(user) as conn:
        return await ticket_repo.get_ticket_detail(conn, id, user)
