"""SLA arithmetic, pause/resume, evaluation and workflow transition rules."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from opsdesk.db.models import Approval, BusinessHours, SLAPolicy, State, Ticket, TicketType, Transition, User
from opsdesk.services import sla, tickets as svc
from opsdesk.services.workflow import TransitionError, available_transitions, validate_transition

UTC = timezone.utc
OFFICE = {str(d): [["09:00", "18:00"]] for d in range(5)}  # Mon-Fri 09:00-18:00


def utc(*args):
    return datetime(*args, tzinfo=UTC)


class TestBusinessCalendar:
    def test_24x7_is_plain_arithmetic(self):
        cal = sla.BusinessCalendar(always_open=True)
        start = utc(2026, 3, 6, 17, 0)
        assert cal.add_minutes(start, 90) == start + timedelta(minutes=90)
        assert cal.minutes_between(start, start + timedelta(hours=5)) == 300

    def test_due_date_skips_the_weekend(self):
        cal = sla.BusinessCalendar("UTC", OFFICE)
        fri_5pm = utc(2026, 3, 6, 17, 0)  # Friday
        assert cal.add_minutes(fri_5pm, 120) == utc(2026, 3, 9, 10, 0)  # 60 min Friday + 60 min Monday

    def test_start_outside_hours_waits_for_opening(self):
        cal = sla.BusinessCalendar("UTC", OFFICE)
        assert cal.add_minutes(utc(2026, 3, 7, 12, 0), 30) == utc(2026, 3, 9, 9, 30)  # Saturday noon

    def test_holidays_are_skipped(self):
        cal = sla.BusinessCalendar("UTC", OFFICE, holidays=["2026-03-09"])
        assert cal.add_minutes(utc(2026, 3, 6, 17, 30), 60) == utc(2026, 3, 10, 9, 30)

    def test_working_minutes_between(self):
        cal = sla.BusinessCalendar("UTC", OFFICE)
        assert cal.minutes_between(utc(2026, 3, 6, 16, 0), utc(2026, 3, 9, 10, 0)) == 180  # 2h Friday + 1h Monday
        assert cal.minutes_between(utc(2026, 3, 7, 0, 0), utc(2026, 3, 8, 23, 0)) == 0
        assert cal.minutes_between(utc(2026, 3, 9, 12, 0), utc(2026, 3, 9, 11, 0)) == 0

    @pytest.mark.parametrize("start,minutes", [(utc(2026, 3, 4, 8, 15), 1), (utc(2026, 3, 4, 10, 0), 499),
                                               (utc(2026, 3, 6, 17, 59), 2000), (utc(2026, 3, 8, 3, 0), 600)])
    def test_add_and_between_are_inverse(self, start, minutes):
        cal = sla.BusinessCalendar("UTC", OFFICE)
        assert cal.minutes_between(start, cal.add_minutes(start, minutes)) == minutes

    def test_timezone_aware_hours(self):
        cal = sla.BusinessCalendar("Asia/Kolkata", OFFICE)  # 09:00 IST = 03:30 UTC
        assert cal.add_minutes(utc(2026, 3, 9, 0, 0), 30) == utc(2026, 3, 9, 4, 0)

    def test_dst_transition_keeps_local_working_hours(self):
        cal = sla.BusinessCalendar("America/New_York", OFFICE)
        # Friday 6 Mar 2026 is EST (UTC-5): 09:00 local = 14:00 UTC. The clocks change on Sunday 8 Mar; Monday is EDT
        # (UTC-4): 09:00 local = 13:00 UTC. The working day starts at the same local time on both sides of the change.
        assert cal.add_minutes(utc(2026, 3, 6, 14, 0), 60) == utc(2026, 3, 6, 15, 0)
        assert cal.add_minutes(utc(2026, 3, 9, 13, 0), 60) == utc(2026, 3, 9, 14, 0)
        assert cal.add_minutes(utc(2026, 3, 6, 22, 30), 60) == utc(2026, 3, 9, 13, 30)  # 30 min left Fri + 30 min Mon

    def test_calendar_without_working_time_raises(self):
        cal = sla.BusinessCalendar("UTC", {"0": []}, always_open=False)
        # an empty schedule means "always open"; a schedule with no intervals cannot compute due dates
        cal.always_open = False
        cal.schedule = {0: []}
        with pytest.raises(ValueError):
            cal.add_minutes(utc(2026, 3, 9, 9, 0), 10)


def _incident(s, w, **kw):
    tt = s.scalars(select(TicketType).where(TicketType.tenant_id == w.tenant_id, TicketType.key == "incident")).one()
    requester = s.get(User, w.users["customer"])
    return svc.create_ticket(s, tenant_id=w.tenant_id, ticket_type=tt, subject=kw.pop("subject", "Disk full"),
                             description="df shows 100%", requester=requester, created_by=requester, **kw)


class TestSlaOnTickets:
    def test_policy_and_due_dates_follow_the_priority_matrix(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world, impact=1, urgency=1)  # P1: first response 15 min, resolution 240 working min
            assert t.priority.key == "P1" and t.sla_status == "on_track"
            assert t.sla_policy.name.startswith("Standard")
            cal = sla.calendar_for(t)
            assert t.response_due_at == cal.add_minutes(t.created_at, 15)
            assert t.resolution_due_at == cal.add_minutes(t.created_at, 240)

    def test_account_policy_overrides_the_default(self, world, session_for):
        from opsdesk.db.models import Account
        with session_for() as s:
            premium = s.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id == world.tenant_id,
                                                       SLAPolicy.name.startswith("Premium"))).one()
            s.get(Account, world.account_id).sla_policy_id = premium.id
            s.flush()
            s.expire_all()
            t = _incident(s, world, impact=1, urgency=1)
            assert t.sla_policy_id == premium.id
            assert t.response_due_at == t.created_at + timedelta(minutes=10)  # 24x7 premium

    def test_pausing_state_stops_the_clock_and_resume_extends_the_due_date(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world, impact=2, urgency=2)
            states = {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}
            svc.set_state(s, t, states["in_progress"])
            due_before = t.resolution_due_at
            svc.set_state(s, t, states["pending_customer"])
            assert t.sla_status == "paused" and t.sla_paused_at is not None
            t.sla_paused_at = utc(2026, 3, 4, 10, 0)  # simulate 2 working hours paused
            cal = sla.calendar_for(t)
            sla.on_state_change(t, states["pending_customer"], states["in_progress"], now=utc(2026, 3, 4, 12, 0))
            assert t.sla_status == "on_track" and t.sla_paused_at is None
            assert t.resolution_due_at == cal.add_minutes(due_before, cal.minutes_between(utc(2026, 3, 4, 10, 0), utc(2026, 3, 4, 12, 0)))

    def test_resolving_inside_the_window_is_met_and_late_is_breached(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world)
            states = {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}
            svc.set_state(s, t, states["resolved"])
            assert t.sla_status == "met" and t.resolved_at is not None
            late = _incident(s, world, subject="Late one")
            late.resolution_due_at = late.created_at - timedelta(hours=1)
            svc.set_state(s, late, states["resolved"])
            assert late.sla_status == "breached" and late.resolution_breached

    def test_reopening_clears_resolution_stamps(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world)
            states = {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}
            svc.set_state(s, t, states["resolved"])
            svc.set_state(s, t, states["in_progress"])
            assert t.resolved_at is None and t.closed_at is None and t.sla_status == "on_track"

    def test_first_response_is_recorded_once_and_flags_a_late_one(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world, impact=1, urgency=1)
            assert sla.record_first_response(t, t.response_due_at + timedelta(minutes=1)) is True
            assert t.response_breached is True
            assert sla.record_first_response(t) is False

    def test_evaluate_moves_through_at_risk_to_breached(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world, impact=1, urgency=1)
            cal = sla.calendar_for(t)
            window = cal.minutes_between(t.created_at, t.resolution_due_at)
            assert sla.evaluate(t, t.created_at + timedelta(minutes=1)) == "on_track"
            t.first_responded_at = t.created_at  # response SLA met, only resolution is timed
            at_risk_time = cal.add_minutes(t.created_at, int(window * 0.8))
            assert sla.evaluate(t, at_risk_time) == "at_risk"
            assert sla.evaluate(t, t.resolution_due_at + timedelta(minutes=1)) == "breached"

    def test_ticket_without_policy_or_priority_target_has_no_sla(self, world, session_for):
        with session_for() as s:
            for p in s.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id == world.tenant_id)):
                p.is_default = False
            s.flush()
            s.expire_all()
            t = _incident(s, world)
            assert t.sla_status == "none" and t.response_due_at is None
            assert sla.evaluate(t) == "none"

    def test_priority_change_rebases_due_dates(self, world, session_for):
        with session_for() as s:
            t = _incident(s, world, impact=3, urgency=3)
            p1 = s.scalars(select(type(t.priority)).where(type(t.priority).tenant_id == world.tenant_id,
                                                          type(t.priority).key == "P1")).one()
            old = t.resolution_due_at
            svc.update_ticket(s, t, None, {"priority": p1})
            assert t.resolution_due_at < old


class TestWorkflowRules:
    def _setup(self, s, w):
        t = _incident(s, w)
        by_name = {tr.name: tr for tr in s.scalars(select(Transition).where(Transition.workflow_id == t.state.workflow_id))}
        return t, by_name, s.get(User, w.users["employee"]), s.get(User, w.users["customer"])

    def test_agent_sees_forward_transitions_only(self, world, session_for):
        with session_for() as s:
            t, tr, agent, _ = self._setup(s, world)
            names = {x.name for x in available_transitions(s, t, agent)}
            assert {"Start work", "Resolve", "Cancel"} <= names and "Reopen" not in names

    def test_customer_only_gets_customer_allowed_transitions(self, world, session_for):
        with session_for() as s:
            t, tr, agent, customer = self._setup(s, world)
            assert available_transitions(s, t, customer) == []
            svc.set_state(s, t, tr["Resolve"].to_state)
            assert {x.name for x in available_transitions(s, t, customer)} == {"Reopen", "Confirm and close"}

    def test_required_fields_comment_and_roles_are_enforced(self, world, session_for):
        with session_for() as s:
            t, tr, agent, customer = self._setup(s, world)
            with pytest.raises(TransitionError, match="resolution_code"):
                validate_transition(t, tr["Resolve"], agent)
            t.resolution_code, t.resolution_notes = "fixed", "cleared logs"
            validate_transition(t, tr["Resolve"], agent)
            svc.set_state(s, t, tr["Start work"].to_state)
            with pytest.raises(TransitionError, match="comment"):
                validate_transition(t, tr["Ask customer"], agent, comment="  ")
            validate_transition(t, tr["Ask customer"], agent, comment="Please send logs")
            with pytest.raises(TransitionError, match="not allowed"):
                validate_transition(t, tr["Resolve"], customer)

    def test_cancel_is_restricted_to_staff_roles(self, world, session_for):
        with session_for() as s:
            t, tr, agent, customer = self._setup(s, world)
            validate_transition(t, tr["Cancel"], agent, comment="dup")
            with pytest.raises(TransitionError):
                validate_transition(t, tr["Cancel"], customer, comment="dup")

    def test_wrong_source_state_and_closed_tickets_are_refused(self, world, session_for):
        with session_for() as s:
            t, tr, agent, _ = self._setup(s, world)
            with pytest.raises(TransitionError, match="not available"):
                validate_transition(t, tr["Confirm and close"], agent)
            closed = s.scalars(select(State).where(State.workflow_id == t.state.workflow_id, State.key == "closed")).one()
            svc.set_state(s, t, closed)
            t.resolution_code, t.resolution_notes = "x", "y"
            with pytest.raises(TransitionError, match="closed"):
                validate_transition(t, tr["Resolve"], agent)

    def test_approval_gate_blocks_until_every_approval_is_granted(self, world, session_for):
        with session_for() as s:
            tt = s.scalars(select(TicketType).where(TicketType.tenant_id == world.tenant_id, TicketType.key == "change")).one()
            requester = s.get(User, world.users["employee"])
            t = svc.create_ticket(s, tenant_id=world.tenant_id, ticket_type=tt, subject="Rotate certs", requester=requester,
                                  created_by=requester)
            states = {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}
            tr = {x.name: x for x in s.scalars(select(Transition).where(Transition.workflow_id == t.state.workflow_id))}
            svc.set_state(s, t, states["cab_review"])
            with pytest.raises(TransitionError, match="Approval required"):
                validate_transition(t, tr["Approve and schedule"], requester)
            s.add(Approval(tenant_id=world.tenant_id, ticket_id=t.id, approver_id=world.users["team_lead"], status="pending"))
            s.flush()
            s.refresh(t)
            with pytest.raises(TransitionError, match="must be approved"):
                validate_transition(t, tr["Approve and schedule"], requester)
            t.approvals[0].status = "approved"
            validate_transition(t, tr["Approve and schedule"], requester)
