"""Decoupled CSAT survey router.

Resolves survey tokens via public.resolve_csat_survey_by_token (SECURITY DEFINER, no tenant needed) and records
responses inside the survey's own tenant.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Request, status
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from ...db.base import utcnow
from ...db.models import CSATSurvey
from ...db.session import plain_session, tenant_session
from ...services import tickets as services
from ..schemas.tickets import CSATSubmitRequest, CSATSurveyResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["csat"])

INVALID = "This survey link isn't valid."


def _handle(method: str, token: str, payload: Optional[CSATSubmitRequest]) -> CSATSurveyResponse:
    try:
        with plain_session() as s:
            row = s.execute(text("SELECT survey_id, tenant_id, ticket_id, score, responded_at "
                                 "FROM public.resolve_csat_survey_by_token(:t)"), {"t": token}).first()
    except Exception as exc:  # noqa: BLE001
        logger.error("CSAT token resolution database failure: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="CSAT service is temporarily unavailable. Please retry shortly.")
    if not row or not row.survey_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVALID)

    with tenant_session(row.tenant_id) as s:
        survey = s.scalars(select(CSATSurvey).where(CSATSurvey.id == row.survey_id)).first()
        if survey is None or survey.ticket is None or survey.ticket.tenant_id != row.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVALID)
        if method == "POST":
            if not payload or not (1 <= payload.score <= 5):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a rating from 1 to 5.")
            survey.score = payload.score
            survey.comment = (payload.comment or "")[:2000]
            survey.responded_at = utcnow()
            s.flush()
            services.log_event(s, survey.ticket, "csat", None, score=payload.score)
        return CSATSurveyResponse(ticket=survey.ticket.number, subject=survey.ticket.subject, score=survey.score,
                                  responded=survey.responded_at is not None)


@router.get("/api/csat/{token}/", response_model=CSATSurveyResponse)
@router.post("/api/csat/{token}/", response_model=CSATSurveyResponse)
@router.get("/api/tickets/csat/{token}/verify/", response_model=CSATSurveyResponse)
@router.get("/api/tickets/csat/{token}/status/", response_model=CSATSurveyResponse)
@router.post("/api/tickets/csat/{token}/submit/", response_model=CSATSurveyResponse)
async def csat_survey(request: Request, token: str = Path(..., min_length=16, max_length=128),
                      payload: Optional[CSATSubmitRequest] = None):
    """Customer satisfaction survey resolution and submission."""
    return await run_in_threadpool(_handle, request.method, token, payload)
