"""AI search, feedback and per-ticket AI endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import or_, select

from ...ai.engine import (detect_duplicates, draft_reply, similar_articles, similar_tickets, summarize, ticket_text,
                          triage)
from ...db.models import AIFeedback, Category, KnowledgeArticle, Team, Ticket, User
from ...db.session import tenant_session
from ...services import tickets as services
from ...services.visibility import visible_ticket_criteria
from ..repositories.tickets import ticket_repo
from ..runtime import run_in_tenant
from ..schemas.ai import DeflectionSearchRequest, DeflectionSearchResponse, FeedbackRequest
from ..schemas.tickets import TicketDetailResponse
from ..security import AuthenticatedUser, get_authenticated_user, get_current_user
from ..tenant_context import tenant_transaction

router = APIRouter(tags=["ai"])


def _nf() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _staff(user: AuthenticatedUser, what: str):
    if not user.is_agent:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Only staff can {what}.")


def _caller(s, user: AuthenticatedUser) -> Optional[User]:
    return s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id)).first()


def _readable_by_requester(s, found, requester):
    """Keep only suggested articles the requester could open in the knowledge base (published, public, shared or
    their own account's). ``similar_articles`` applies no account filter when the ticket or requester has no
    account."""
    readable = set(s.scalars(select(KnowledgeArticle.id).where(
        KnowledgeArticle.id.in_([a["id"] for a in found]), KnowledgeArticle.status == "published",
        KnowledgeArticle.visibility == "public",
        or_(KnowledgeArticle.account_id.is_(None),
            KnowledgeArticle.account_id == (requester.account_id if requester else None)))))
    return [a for a in found if a["id"] in readable]


@router.post("/api/ai/search/", response_model=DeflectionSearchResponse)
def deflection_search(payload: DeflectionSearchRequest, user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Live suggestions while typing a new ticket: past resolved tickets and KB articles. Requesters use it on the
    raise-a-request form: tickets are limited to ones they can see and articles to ones they could read."""
    text = (payload.text or "").strip()
    if len(text) < 12:
        return DeflectionSearchResponse(tickets=[], articles=[])
    with tenant_session(user.tenant_id, commit=False) as s:
        me = _caller(s, user)
        account = me.account if (me and not user.is_agent and me.account_id) else None
        tickets = similar_tickets(s, user.tenant_id, text=text, user=me, resolved_only=True, limit=4)
        articles = similar_articles(s, user.tenant_id, text, public_only=not user.is_agent, account=account, limit=3)
        if not user.is_agent:
            articles = _readable_by_requester(s, articles, me)
        return DeflectionSearchResponse(tickets=tickets, articles=articles)


@router.post("/api/ai/feedback/", status_code=status.HTTP_201_CREATED)
def submit_ai_feedback(payload: FeedbackRequest, user: AuthenticatedUser = Depends(get_current_user)):
    """Record user feedback on AI features."""
    with tenant_session(user.tenant_id) as s:
        me = _caller(s, user)
        s.add(AIFeedback(tenant_id=user.tenant_id, ticket_id=payload.ticket_id, feature=payload.feature,
                         suggestion=payload.suggestion, accepted=payload.accepted, user_id=me.id if me else None))
    return {"ok": True}


def _ticket(s, id: int, tenant_id: int) -> Ticket:
    t = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == tenant_id)).first()
    if not t:
        raise _nf()
    return t


@router.get("/api/tickets/{id}/similar/")
def ticket_similar(id: int = Path(..., ge=1), resolved: Optional[str] = Query("0"), limit: int = Query(6, ge=1, le=20),
                   user: AuthenticatedUser = Depends(get_current_user)):
    """Similar tickets by vector similarity."""
    with tenant_session(user.tenant_id) as s:  # may compute and store the ticket's embedding
        me = _caller(s, user)
        ticket = _ticket(s, id, user.tenant_id)
        return similar_tickets(s, user.tenant_id, ticket=ticket, user=me, resolved_only=resolved == "1", limit=limit)


@router.get("/api/tickets/{id}/kb_suggestions/")
def ticket_kb_suggestions(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_authenticated_user)):
    """Relevant KB articles for a ticket. Requesters may use it on their own tickets: the ticket must be one they can
    see, and suggestions are limited to articles they could read."""
    with tenant_session(user.tenant_id, commit=False) as s:
        requester = None
        if user.is_agent:
            ticket = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        else:
            requester = _caller(s, user)
            ticket = (s.scalars(select(Ticket).where(Ticket.id == id, visible_ticket_criteria(s, requester))).first()
                      if requester else None)
        if not ticket:
            raise _nf()
        found = similar_articles(s, user.tenant_id, ticket_text(ticket), public_only=not user.is_agent,
                                 account=ticket.account if ticket.account_id else None)
        return found if user.is_agent else _readable_by_requester(s, found, requester)


@router.post("/api/tickets/{id}/ai_summary/")
def ticket_ai_summary(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Concise AI summary of a ticket."""
    _staff(user, "request AI summaries")
    with tenant_session(user.tenant_id) as s:
        ticket = _ticket(s, id, user.tenant_id)
        text = summarize(s, ticket)
        ticket.ai_summary = text
        return {"summary": text}


@router.post("/api/tickets/{id}/ai_draft/")
def ticket_ai_draft(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """AI draft reply for a ticket."""
    _staff(user, "request AI draft replies")
    with tenant_session(user.tenant_id, commit=False) as s:
        me = _caller(s, user)
        return draft_reply(s, _ticket(s, id, user.tenant_id), me)


@router.post("/api/tickets/{id}/ai_triage/")
def ticket_ai_triage(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """AI triage predictions and duplicate detection."""
    _staff(user, "run AI triage")
    with tenant_session(user.tenant_id) as s:
        ticket = _ticket(s, id, user.tenant_id)
        data = {**triage(s, ticket), "possible_duplicates": detect_duplicates(s, ticket)}
        ticket.ai_triage = data
        return data


@router.post("/api/tickets/{id}/apply_triage/", response_model=TicketDetailResponse)
async def ticket_apply_triage(id: int = Path(..., ge=1), user: AuthenticatedUser = Depends(get_current_user)):
    """Apply suggested AI triage predictions to a ticket."""
    _staff(user, "apply AI triage")

    def _apply(s):
        me = _caller(s, user)
        ticket = s.scalars(select(Ticket).where(Ticket.id == id, Ticket.tenant_id == user.tenant_id)).first()
        if not ticket or not me:
            raise _nf()
        sug = ticket.ai_triage or {}
        changes = {}
        if sug.get("category_id"):
            changes["category"] = s.scalars(select(Category).where(Category.id == sug["category_id"],
                                                                   Category.tenant_id == ticket.tenant_id)).first()
        if sug.get("team_id"):
            changes["team"] = s.scalars(select(Team).where(Team.id == sug["team_id"],
                                                           Team.tenant_id == ticket.tenant_id)).first()
        for f in ("impact", "urgency"):
            if sug.get(f):
                changes[f] = int(sug[f])
        changes = {k: v for k, v in changes.items() if v is not None}
        services.update_ticket(s, ticket, me, changes)
        s.add(AIFeedback(tenant_id=ticket.tenant_id, ticket_id=ticket.id, feature="triage", suggestion=sug,
                         accepted=True, user_id=me.id))

    await run_in_tenant(user, _apply)
    async with tenant_transaction(user) as conn:
        return await ticket_repo.get_ticket_detail(conn, id, user)
