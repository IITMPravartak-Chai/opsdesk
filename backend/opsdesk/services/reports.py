"""KPI aggregation and export rows for the reports pages. Everything is scoped by ticket visibility."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session, joinedload

from ..db.base import utcnow
from ..db.models import (AIFeedback, Account, CSATSurvey, Priority, State, Team, Ticket, TicketType, User)
from .visibility import visible_ticket_criteria

DONE = ["resolved", "closed", "cancelled"]


def _hours(td) -> Optional[float]:
    return round(td.total_seconds() / 3600, 1) if td else None


def _base(session: Session, actor: User, account: Optional[int]):
    conds = [visible_ticket_criteria(session, actor)]
    if account is not None:
        conds.append(Ticket.account_id == account)
    return conds


def _count(session: Session, *conds) -> int:
    return session.scalar(select(func.count()).select_from(Ticket).join(State, State.id == Ticket.state_id).where(*conds)) or 0


def overview(session: Session, actor: User, days: int, account: Optional[int] = None) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    base = _base(session, actor, account)
    not_done = State.category.notin_(DONE)

    open_c = [*base, not_done]
    period_c = [*base, Ticket.created_at >= since]
    resolved_c = [*base, Ticket.resolved_at >= since]

    sla_total = _count(session, *resolved_c, Ticket.sla_status != "none")
    sla_met = _count(session, *resolved_c, Ticket.sla_status != "none", Ticket.resolution_breached.is_(False),
                     Ticket.response_breached.is_(False))
    mttr = session.scalar(select(func.avg(Ticket.resolved_at - Ticket.created_at)).join(
        State, State.id == Ticket.state_id).where(*resolved_c))
    frt = session.scalar(select(func.avg(Ticket.first_responded_at - Ticket.created_at)).join(
        State, State.id == Ticket.state_id).where(*period_c, Ticket.first_responded_at.is_not(None)))

    def series_of(col, conds) -> dict:
        day = cast(func.timezone("UTC", col), Date)
        rows = session.execute(select(day, func.count()).select_from(Ticket).join(State, State.id == Ticket.state_id)
                               .where(*conds).group_by(day)).all()
        return {d: n for d, n in rows}

    created_series = series_of(Ticket.created_at, period_c)
    resolved_series = series_of(Ticket.resolved_at, resolved_c)
    series = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).date()
        series.append({"date": d.isoformat(), "created": created_series.get(d, 0), "resolved": resolved_series.get(d, 0)})

    aging = {"< 1 day": 0, "1-3 days": 0, "3-7 days": 0, "7-30 days": 0, "> 30 days": 0}
    for (c,) in session.execute(select(Ticket.created_at).join(State, State.id == Ticket.state_id).where(*open_c)):
        age = (now - c).days
        key = ("< 1 day" if age < 1 else "1-3 days" if age < 3 else "3-7 days" if age < 7
               else "7-30 days" if age < 30 else "> 30 days")
        aging[key] += 1

    visible_ids = select(Ticket.id).where(*base)
    csat_c = [CSATSurvey.tenant_id == actor.tenant_id, CSATSurvey.responded_at >= since, CSATSurvey.ticket_id.in_(visible_ids)]
    csat_n = session.scalar(select(func.count()).select_from(CSATSurvey).where(*csat_c)) or 0
    csat_avg = session.scalar(select(func.avg(CSATSurvey.score)).where(*csat_c)) if csat_n else None
    fb_c = [AIFeedback.tenant_id == actor.tenant_id, AIFeedback.created_at >= since]
    fb_total = session.scalar(select(func.count()).select_from(AIFeedback).where(*fb_c)) or 0
    fb_ok = session.scalar(select(func.count()).select_from(AIFeedback).where(*fb_c, AIFeedback.accepted.is_(True))) or 0

    def grouped(cols, conds, order, limit=None):
        stmt = (select(*cols, func.count().label("count")).select_from(Ticket).join(State, State.id == Ticket.state_id)
                .outerjoin(Priority, Priority.id == Ticket.priority_id).outerjoin(Team, Team.id == Ticket.team_id)
                .outerjoin(Account, Account.id == Ticket.account_id).where(*conds))
        stmt = stmt.group_by(*cols).order_by(*order)
        if limit:
            stmt = stmt.limit(limit)
        return session.execute(stmt).all()

    by_priority = [{"key": r[0], "color": r[1], "count": r[2]}
                   for r in grouped([Priority.key, Priority.color], open_c, [Priority.key])]
    by_state = [{"name": r[0], "color": r[1], "count": r[2]}
                for r in grouped([State.name, State.color], open_c, [func.count().desc()])]
    by_source = [{"source": r[0], "count": r[1]} for r in grouped([Ticket.source], period_c, [func.count().desc()])]
    breached = func.count().filter(Ticket.sla_status == "breached")
    by_account = [{"name": r[0], "count": r[1], "breached": r[2]}
                  for r in session.execute(
                      select(Account.name, func.count(), breached).select_from(Ticket).join(State, State.id == Ticket.state_id)
                      .outerjoin(Account, Account.id == Ticket.account_id).where(*period_c)
                      .group_by(Account.name).order_by(func.count().desc()).limit(8)).all()]
    by_team = [{"name": r[0], "count": r[1]} for r in grouped([Team.name], open_c, [func.count().desc()], 8)]

    return {
        "period_days": days,
        "open": _count(session, *open_c),
        "unassigned": _count(session, *open_c, Ticket.assignee_id.is_(None)),
        "at_risk": _count(session, *open_c, Ticket.sla_status == "at_risk"),
        "breached_open": _count(session, *open_c, Ticket.sla_status == "breached"),
        "major_incidents": _count(session, *open_c, Ticket.is_major_incident.is_(True)),
        "created": _count(session, *period_c),
        "resolved": _count(session, *resolved_c),
        "sla_compliance_pct": round(100 * sla_met / sla_total, 1) if sla_total else None,
        "mttr_hours": _hours(mttr),
        "first_response_hours": _hours(frt),
        "csat_avg": round(float(csat_avg or 0), 2) if csat_n else None,
        "csat_responses": csat_n,
        "ai_acceptance_pct": round(100 * fb_ok / fb_total, 1) if fb_total else None,
        "series": series,
        "aging": [{"bucket": k, "count": v} for k, v in aging.items()],
        "by_priority": by_priority, "by_state": by_state, "by_source": by_source,
        "by_account": by_account, "by_team": by_team,
    }


def build_export_report(session: Session, user: User, report_type: str, days: int, account: Optional[int] = None):
    """Rows for an export, as (title, subtitle, headers, rows, generated_at). Scoped by ticket visibility.
    Raises ValueError for an unknown report type."""
    now = utcnow()
    since = now - timedelta(days=days)
    base = _base(session, user, account)
    not_done = State.category.notin_(DONE)

    headers: list = []
    rows: list = []
    title = "OpsDesk Report"
    subtitle = f"Period: Last {days} days | Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"

    if report_type == "overview":
        title = "Service Desk KPIs & Overview"
        open_c = [*base, not_done]
        period_c = [*base, Ticket.created_at >= since]
        resolved_c = [*base, Ticket.resolved_at >= since]
        sla_total = _count(session, *resolved_c, Ticket.sla_status != "none")
        sla_met = _count(session, *resolved_c, Ticket.sla_status != "none", Ticket.resolution_breached.is_(False),
                         Ticket.response_breached.is_(False))
        mttr = session.scalar(select(func.avg(Ticket.resolved_at - Ticket.created_at)).join(
            State, State.id == Ticket.state_id).where(*resolved_c))
        frt = session.scalar(select(func.avg(Ticket.first_responded_at - Ticket.created_at)).join(
            State, State.id == Ticket.state_id).where(*period_c, Ticket.first_responded_at.is_not(None)))
        headers = ["Metric", "Value"]
        rows = [
            ["Reporting Period", f"{days} days"],
            ["Total Open Tickets", str(_count(session, *open_c))],
            ["Unassigned Open Tickets", str(_count(session, *open_c, Ticket.assignee_id.is_(None)))],
            ["SLA Breached (Open)", str(_count(session, *open_c, Ticket.sla_status == "breached"))],
            ["SLA At Risk", str(_count(session, *open_c, Ticket.sla_status == "at_risk"))],
            ["Major Incidents (Open)", str(_count(session, *open_c, Ticket.is_major_incident.is_(True)))],
            ["Tickets Created in Period", str(_count(session, *period_c))],
            ["Tickets Resolved in Period", str(_count(session, *resolved_c))],
            ["SLA Compliance Rate", f"{round(100 * sla_met / sla_total, 1)}%" if sla_total else "N/A"],
            ["Mean Time to Resolve (MTTR)", f"{_hours(mttr)} hrs" if mttr else "N/A"],
            ["Average First Response Time", f"{_hours(frt)} hrs" if frt else "N/A"],
        ]

    elif report_type == "tickets":
        title = "Tickets Report"
        headers = ["Number", "Subject", "State", "Priority", "Type", "Team", "Assignee", "Account", "SLA Status",
                   "Major Incident", "Created At"]
        stmt = (select(Ticket).options(joinedload(Ticket.state), joinedload(Ticket.priority),
                                       joinedload(Ticket.ticket_type), joinedload(Ticket.team),
                                       joinedload(Ticket.assignee), joinedload(Ticket.account))
                .where(*base, Ticket.created_at >= since).order_by(Ticket.created_at.desc()).limit(1000))
        for t in session.scalars(stmt).unique():
            rows.append([
                f"#{t.number}", t.subject, t.state.name if t.state else "-", t.priority.name if t.priority else "-",
                t.ticket_type.name if t.ticket_type else "-", t.team.name if t.team else "Unassigned",
                (t.assignee.get_full_name() or t.assignee.username) if t.assignee else "Unassigned",
                t.account.name if t.account else "Internal", t.sla_status, "Yes" if t.is_major_incident else "No",
                t.created_at.strftime("%Y-%m-%d %H:%M"),
            ])

    elif report_type == "incidents":
        title = "Major Incidents Report"
        headers = ["Number", "Subject", "Major Status", "Commander", "State", "Priority", "Created At", "Resolved At"]
        stmt = (select(Ticket).options(joinedload(Ticket.state), joinedload(Ticket.priority),
                                       joinedload(Ticket.incident_commander))
                .where(*base, Ticket.is_major_incident.is_(True)).order_by(Ticket.created_at.desc()).limit(500))
        for inc in session.scalars(stmt).unique():
            rows.append([
                f"#{inc.number}", inc.subject, inc.major_incident_status or "declared",
                (inc.incident_commander.get_full_name() or inc.incident_commander.username) if inc.incident_commander else "Unassigned",
                inc.state.name if inc.state else "-", inc.priority.name if inc.priority else "-",
                inc.created_at.strftime("%Y-%m-%d %H:%M"),
                inc.resolved_at.strftime("%Y-%m-%d %H:%M") if inc.resolved_at else "Unresolved",
            ])

    elif report_type == "workload":
        title = "Team Workload Report"
        headers = ["Team", "Assignee", "Open Tickets", "Breached SLA"]
        stmt = (select(Team.name, User.username, func.count().label("n"), func.count().filter(Ticket.sla_status == "breached"))
                .select_from(Ticket).join(State, State.id == Ticket.state_id)
                .outerjoin(Team, Team.id == Ticket.team_id).outerjoin(User, User.id == Ticket.assignee_id)
                .where(*base, not_done).group_by(Team.name, User.username).order_by(func.count().desc()).limit(200))
        for team_name, assignee_name, open_count, breached_count in session.execute(stmt):
            rows.append([team_name or "No Team", assignee_name or "Unassigned", str(open_count), str(breached_count)])

    else:
        raise ValueError(f"Unknown report type '{report_type}'. Supported: overview, tickets, incidents, workload.")

    return title, subtitle, headers, rows, now
