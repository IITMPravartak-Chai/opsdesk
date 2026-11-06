"""SLA engine: business-hours arithmetic, due-date calculation, pause/resume and at-risk/breach evaluation.
All arithmetic is done in UTC; calendar boundaries are computed in the calendar's local timezone so DST is handled."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..db.models import SLAPolicy, SLAPolicyTicketType, SLATarget, State, Ticket

UTC = dt_tz.utc
MAX_DAYS_SCAN = 800
DONE_CATEGORIES = ("resolved", "closed", "cancelled")


class BusinessCalendar:
    def __init__(self, tz="UTC", schedule=None, holidays=None, always_open=False):
        self.tz = ZoneInfo(tz or "UTC")
        self.always_open = always_open or not schedule
        self.schedule = {}
        for day, ranges in (schedule or {}).items():
            self.schedule[int(day)] = [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in ranges]
        self.holidays = {date.fromisoformat(h) for h in (holidays or [])}

    @classmethod
    def from_model(cls, bh):
        if bh is None or bh.is_24x7:
            return cls(always_open=True)
        return cls(bh.timezone, bh.schedule, bh.holidays)

    def _intervals_for(self, d):
        """UTC (start, end) working intervals on local date d."""
        if d in self.holidays:
            return []
        out = []
        for a, b in self.schedule.get(d.weekday(), []):
            start = datetime.combine(d, a, tzinfo=self.tz).astimezone(UTC)
            end = (datetime.combine(d + timedelta(days=1), time(0), tzinfo=self.tz) if b == time(0)
                   else datetime.combine(d, b, tzinfo=self.tz)).astimezone(UTC)
            if end > start:
                out.append((start, end))
        return out

    def add_minutes(self, start, minutes):
        """Return the instant `minutes` working minutes after `start`."""
        if self.always_open:
            return start + timedelta(minutes=minutes)
        remaining = timedelta(minutes=minutes)
        cursor = start.astimezone(UTC)
        d = cursor.astimezone(self.tz).date()
        for _ in range(MAX_DAYS_SCAN):
            for a, b in self._intervals_for(d):
                if cursor >= b:
                    continue
                s = max(cursor, a)
                avail = b - s
                if remaining <= avail:
                    return s + remaining
                remaining -= avail
                cursor = b
            d += timedelta(days=1)
        raise ValueError("Business calendar has no working time")

    def minutes_between(self, start, end):
        """Working minutes in [start, end)."""
        if end <= start:
            return 0
        if self.always_open:
            return int((end - start).total_seconds() // 60)
        total = timedelta()
        d = start.astimezone(self.tz).date()
        last = end.astimezone(self.tz).date()
        while d <= last:
            for a, b in self._intervals_for(d):
                s, e = max(a, start), min(b, end)
                if e > s:
                    total += e - s
            d += timedelta(days=1)
        return int(total.total_seconds() // 60)


def calendar_for(ticket) -> BusinessCalendar:
    bh = None
    if ticket.sla_policy is not None and ticket.sla_policy.business_hours is not None:
        bh = ticket.sla_policy.business_hours
    elif ticket.account is not None and ticket.account.business_hours is not None:
        bh = ticket.account.business_hours
    return BusinessCalendar.from_model(bh)


def resolve_policy(session: Session, ticket):
    ticket_type_id = ticket.ticket_type_id or (ticket.ticket_type.id if ticket.ticket_type else None)
    acct = ticket.account
    if acct is not None and acct.sla_policy is not None:
        pol = acct.sla_policy
        types = pol.ticket_types
        if not types or any(t.id == ticket_type_id for t in types):
            return pol
    base = select(SLAPolicy).where(SLAPolicy.tenant_id == ticket.tenant_id, SLAPolicy.is_default.is_(True))
    typed = session.scalars(
        base.join(SLAPolicyTicketType, SLAPolicyTicketType.slapolicy_id == SLAPolicy.id)
        .where(SLAPolicyTicketType.tickettype_id == ticket_type_id).order_by(SLAPolicy.id).limit(1)).first()
    if typed:
        return typed
    untyped = ~select(SLAPolicyTicketType.id).where(SLAPolicyTicketType.slapolicy_id == SLAPolicy.id).exists()
    return session.scalars(base.where(untyped).order_by(SLAPolicy.id).limit(1)).first()


def apply_sla(session: Session, ticket, recompute: bool = False) -> None:
    """Set due dates from the policy target for the ticket's priority. Called on create and whenever priority
    changes (due dates are re-based on created_at)."""
    policy = resolve_policy(session, ticket)
    ticket.sla_policy = policy
    priority_id = ticket.priority_id or (ticket.priority.id if ticket.priority else None)
    if not policy or not priority_id:
        ticket.sla_status = "none"
        ticket.response_due_at = ticket.resolution_due_at = None
        return
    target = session.scalars(select(SLATarget).where(SLATarget.policy_id == policy.id,
                                                     SLATarget.priority_id == priority_id)).first()
    if not target:
        ticket.sla_status = "none"
        return
    cal = calendar_for(ticket)
    base = ticket.created_at or utcnow()
    ticket.response_due_at = cal.add_minutes(base, target.first_response_minutes)
    ticket.resolution_due_at = cal.add_minutes(base, target.resolution_minutes)
    if not recompute or ticket.sla_status in ("none", "", None):
        ticket.sla_status = "on_track"


def on_state_change(ticket, old_state, new_state, now=None) -> None:
    now = now or utcnow()
    # Pause / resume
    if new_state.pauses_sla and not (old_state and old_state.pauses_sla) and not ticket.sla_paused_at:
        ticket.sla_paused_at = now
        if ticket.sla_status not in ("none", "breached", "met"):
            ticket.sla_status = "paused"
    elif old_state and old_state.pauses_sla and not new_state.pauses_sla and ticket.sla_paused_at:
        cal = calendar_for(ticket)
        paused = cal.minutes_between(ticket.sla_paused_at, now)
        if ticket.resolution_due_at:
            ticket.resolution_due_at = cal.add_minutes(ticket.resolution_due_at, paused)
        ticket.sla_paused_at = None
        if ticket.sla_status == "paused":
            ticket.sla_status = "on_track"
    # Resolve / reopen
    if new_state.category == "resolved" and not ticket.resolved_at:
        ticket.resolved_at = now
    if new_state.category in ("resolved", "closed") and ticket.sla_status != "none":
        ticket.resolution_breached = bool(ticket.resolution_due_at and (ticket.resolved_at or now) > ticket.resolution_due_at)
        ticket.sla_status = "breached" if (ticket.resolution_breached or ticket.response_breached) else "met"
        ticket.sla_paused_at = None
    if new_state.category in ("closed", "cancelled") and not ticket.closed_at:
        ticket.closed_at = now
    if old_state and old_state.is_done and not new_state.is_done:
        ticket.resolved_at = ticket.closed_at = None
        if ticket.sla_status == "met":
            ticket.sla_status = "on_track"


def record_first_response(ticket, when=None) -> bool:
    if ticket.first_responded_at:
        return False
    ticket.first_responded_at = when or utcnow()
    if ticket.response_due_at and ticket.first_responded_at > ticket.response_due_at:
        ticket.response_breached = True
    return True


def evaluate(ticket, now=None) -> str:
    """Returns the new sla_status (does not save)."""
    now = now or utcnow()
    if ticket.sla_status in ("none", "met", "paused") or ticket.state.is_done:
        return ticket.sla_status
    if not ticket.first_responded_at and ticket.response_due_at and now > ticket.response_due_at:
        ticket.response_breached = True
    if ticket.resolution_due_at and now > ticket.resolution_due_at:
        ticket.resolution_breached = True
    if ticket.response_breached or ticket.resolution_breached:
        return "breached"
    pct = (ticket.sla_policy.at_risk_percent if ticket.sla_policy_id else 75) / 100
    cal = calendar_for(ticket)
    for due, done in ((ticket.response_due_at, ticket.first_responded_at), (ticket.resolution_due_at, None)):
        if due and not done:
            total = cal.minutes_between(ticket.created_at, due) or 1
            used = cal.minutes_between(ticket.created_at, now)
            if used / total >= pct:
                return "at_risk"
    return "on_track"


def active_sla_clause():
    """SQLAlchemy criterion for tickets whose SLA clock is running (``Ticket`` must be joined to ``State``)."""
    return and_(Ticket.sla_status.in_(["on_track", "at_risk"]), State.category.notin_(DONE_CATEGORIES))
