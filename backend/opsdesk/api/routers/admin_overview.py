"""Read-only Administration overview: one summary of what the five Administration pages hold.

GET /api/admin/overview/   users, teams, SLA, configuration and audit-log figures for the caller's tenant

Super admin only (router-level ``require_administration``). The tenant comes from the signed token, never a parameter,
and every query is tenant-scoped on top of row-level security. Nothing here writes, and no secret or credential is read:
the audit entries show the action, the actor's display name and the time only.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ...db.base import utcnow
from ...db.models import AuditLog, BusinessHours, SLAPolicy, SLATarget, Team, TeamMember, User
from ..config_resources import RESOURCES
from ..runtime import run_in_tenant
from ..security import AuthenticatedUser, get_current_user, require_administration

router = APIRouter(prefix="/api/admin/overview", tags=["admin-overview"], dependencies=[Depends(require_administration)])

ROLES = ("super_admin", "manager", "team_lead", "employee", "customer")
AUDIT_DAYS = 14


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _count(s, model, *conds) -> int:
    return s.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _users(s, tid, now):
    base = [User.tenant_id == tid, User.is_superuser.is_(False)]
    by_role = dict.fromkeys(ROLES, 0)
    for role, n in s.execute(select(User.role, func.count()).where(*base, User.is_active.is_(True)).group_by(User.role)):
        by_role[role] = n
    return {
        "total": _count(s, User, *base),
        "active": _count(s, User, *base, User.is_active.is_(True)),
        "deactivated": _count(s, User, *base, User.is_active.is_(False)),
        "by_role": [{"role": r, "count": by_role[r]} for r in ROLES],
        "two_step_enabled": _count(s, User, *base, User.is_active.is_(True), User.mfa_enabled.is_(True)),
        "never_signed_in": _count(s, User, *base, User.is_active.is_(True), User.last_login.is_(None)),
        "signed_in_7d": _count(s, User, *base, User.is_active.is_(True), User.last_login >= now - timedelta(days=7)),
    }


def _teams(s, tid):
    members = dict(s.execute(select(TeamMember.team_id, func.count()).join(Team, Team.id == TeamMember.team_id)
                             .where(Team.tenant_id == tid).group_by(TeamMember.team_id)).all())
    teams = s.scalars(select(Team).where(Team.tenant_id == tid).order_by(Team.name, Team.id)).all()
    by_strategy: dict = {}
    for t in teams:
        by_strategy[t.assignment_strategy] = by_strategy.get(t.assignment_strategy, 0) + 1
    rows = sorted(({"id": t.id, "name": t.name, "members": members.get(t.id, 0), "strategy": t.assignment_strategy}
                   for t in teams), key=lambda r: (-r["members"], r["name"]))
    return {"total": len(teams), "without_members": sum(1 for t in teams if not members.get(t.id)),
            "by_strategy": [{"strategy": k, "count": v} for k, v in sorted(by_strategy.items())], "largest": rows[:8]}


def _sla(s, tid):
    targets = dict(s.execute(select(SLATarget.policy_id, func.count()).join(SLAPolicy, SLAPolicy.id == SLATarget.policy_id)
                             .where(SLAPolicy.tenant_id == tid).group_by(SLATarget.policy_id)).all())
    policies = s.scalars(select(SLAPolicy).options(joinedload(SLAPolicy.business_hours), joinedload(SLAPolicy.ticket_types))
                         .where(SLAPolicy.tenant_id == tid).order_by(SLAPolicy.name, SLAPolicy.id)).unique().all()
    return {
        "policies": [{"id": p.id, "name": p.name, "is_default": p.is_default, "at_risk_percent": p.at_risk_percent,
                      "business_hours": p.business_hours.name if p.business_hours else None,
                      "is_24x7": bool(p.business_hours and p.business_hours.is_24x7),
                      "targets": targets.get(p.id, 0), "ticket_types": sorted(t.name for t in p.ticket_types)}
                     for p in policies],
        "business_hours": _count(s, BusinessHours, BusinessHours.tenant_id == tid),
    }


def _config(s, tid):
    rows = []
    for slug, res in RESOURCES.items():
        model = res.model
        if not hasattr(model, "tenant_id"):  # child tables are counted through their parent's page
            continue
        total = _count(s, model, model.tenant_id == tid)
        row = {"slug": slug, "label": res.label or slug.replace("-", " ").title(), "total": total}
        if hasattr(model, "is_active"):
            row["active"] = _count(s, model, model.tenant_id == tid, model.is_active.is_(True))
        rows.append(row)
    return rows


def _audit(s, tid, now):
    since = now - timedelta(days=AUDIT_DAYS)
    day = func.date(AuditLog.created_at)
    series = dict(s.execute(select(day, func.count()).where(AuditLog.tenant_id == tid, AuditLog.created_at >= since)
                            .group_by(day)).all())
    days = [(since + timedelta(days=i + 1)).date() for i in range(AUDIT_DAYS)]
    top = s.execute(select(AuditLog.action, func.count().label("n")).where(
        AuditLog.tenant_id == tid, AuditLog.created_at >= now - timedelta(days=7)).group_by(AuditLog.action)
        .order_by(func.count().desc(), AuditLog.action).limit(6)).all()
    recent = s.scalars(select(AuditLog).options(joinedload(AuditLog.actor)).where(AuditLog.tenant_id == tid)
                       .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(8)).unique().all()
    return {
        "last_24h": _count(s, AuditLog, AuditLog.tenant_id == tid, AuditLog.created_at >= now - timedelta(hours=24)),
        "last_7d": _count(s, AuditLog, AuditLog.tenant_id == tid, AuditLog.created_at >= now - timedelta(days=7)),
        "series": [{"date": d.isoformat(), "count": int(series.get(d, 0))} for d in days],
        "top_actions": [{"action": a, "count": n} for a, n in top],
        "recent": [{"id": r.id, "action": r.action, "created_at": _iso(r.created_at),
                    "actor": r.actor.display_name if r.actor is not None else None} for r in recent],
    }


@router.get("/")
async def overview(user: AuthenticatedUser = Depends(get_current_user)):
    def _query(s):
        now, tid = utcnow(), user.tenant_id
        return JSONResponse({"users": _users(s, tid, now), "teams": _teams(s, tid), "sla": _sla(s, tid),
                             "config": _config(s, tid), "audit": _audit(s, tid, now), "generated_at": _iso(now)})

    return await run_in_tenant(user, _query, commit=False)
