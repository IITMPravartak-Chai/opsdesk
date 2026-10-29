"""Reports and analytics: KPI overview, derived report slices and CSV/PDF export."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from ...db.models import User
from ...db.session import tenant_session
from ...services import reports as report_service
from ...services.csv_util import generate_csv
from ...services.pdf import generate_pdf
from ..schemas.reports import OverviewReportResponse
from ..security import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _account(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    if not str(value).isdigit():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid account.")
    return int(value)


def _overview(days: int, account: Optional[str], user: AuthenticatedUser) -> dict:
    if not user.is_agent:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only staff can view reports.")
    acct = _account(account)
    with tenant_session(user.tenant_id, commit=False) as s:
        me = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id)).first()
        if not me:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
        return report_service.overview(s, me, days, acct)


@router.get("/overview/", response_model=OverviewReportResponse)
def get_overview_report(days: int = Query(30, ge=1, le=365, description="Period in days"),
                        account: Optional[str] = Query(None, description="Filter by account id"),
                        user: AuthenticatedUser = Depends(get_current_user)):
    """Service desk KPIs: backlog, flow, SLA compliance, MTTR, first response, CSAT, channel mix, account hotspots
    and AI suggestion acceptance."""
    return _overview(days, account, user)


@router.get("/tickets/volume/")
def get_ticket_volume_report(days: int = Query(30, ge=1, le=365), account: Optional[str] = Query(None),
                             user: AuthenticatedUser = Depends(get_current_user)):
    r = _overview(days, account, user)
    return {"period_days": r["period_days"], "created": r["created"], "resolved": r["resolved"], "series": r["series"]}


@router.get("/tickets/status-breakdown/")
def get_status_breakdown_report(days: int = Query(30, ge=1, le=365), account: Optional[str] = Query(None),
                                user: AuthenticatedUser = Depends(get_current_user)):
    r = _overview(days, account, user)
    return {"period_days": r["period_days"], "open": r["open"], "by_state": r["by_state"], "by_priority": r["by_priority"]}


@router.get("/tickets/sla-compliance/")
def get_sla_compliance_report(days: int = Query(30, ge=1, le=365), account: Optional[str] = Query(None),
                              user: AuthenticatedUser = Depends(get_current_user)):
    r = _overview(days, account, user)
    return {"period_days": r["period_days"], "sla_compliance_pct": r["sla_compliance_pct"], "mttr_hours": r["mttr_hours"],
            "first_response_hours": r["first_response_hours"], "at_risk": r["at_risk"], "breached_open": r["breached_open"]}


@router.get("/csat/summary/")
def get_csat_summary_report(days: int = Query(30, ge=1, le=365), account: Optional[str] = Query(None),
                            user: AuthenticatedUser = Depends(get_current_user)):
    r = _overview(days, account, user)
    return {"period_days": r["period_days"], "csat_avg": r["csat_avg"], "csat_responses": r["csat_responses"]}


@router.get("/agents/performance/")
def get_agent_performance_report(days: int = Query(30, ge=1, le=365), account: Optional[str] = Query(None),
                                 user: AuthenticatedUser = Depends(get_current_user)):
    r = _overview(days, account, user)
    return {"period_days": r["period_days"], "unassigned": r["unassigned"], "by_team": r["by_team"]}


@router.get("/export/")
def export_reports(report: str = Query("overview", description="overview, tickets, incidents or workload"),
                   format: str = Query("csv", description="csv or pdf"), days: Optional[str] = Query("30"),
                   account: Optional[str] = Query(None), user: AuthenticatedUser = Depends(get_current_user)):
    """CSV or PDF export: tenant-isolated via ticket visibility, CSV cells sanitised against formula injection
    (CWE-1236), PDF generated without external dependencies. Staff only."""
    fmt = (format or "csv").lower()
    if fmt not in ("csv", "pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Supported formats: csv, pdf.")
    try:
        period = min(max(1, int(days)), 365)
    except (ValueError, TypeError):
        period = 30
    kind = (report or "overview").lower()
    acct = _account(account)

    with tenant_session(user.tenant_id, commit=False) as s:
        me = s.scalars(select(User).where(User.id == user.user_id, User.tenant_id == user.tenant_id)).first()
        if not me:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
        try:
            title, subtitle, headers, rows, now = report_service.build_export_report(s, me, kind, period, acct)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    stamp = now.strftime("%Y%m%d_%H%M")
    if fmt == "pdf":
        content, media, name = generate_pdf(title, headers, rows, subtitle=subtitle), "application/pdf", f"opsdesk_{kind}_{stamp}.pdf"
    else:
        content, media, name = generate_csv(headers, rows), "text/csv; charset=utf-8", f"opsdesk_{kind}_{stamp}.csv"
    return Response(content=content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-cache, no-store, must-revalidate"})
