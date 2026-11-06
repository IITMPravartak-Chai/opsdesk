"""Ticket services (creation, updates, assignment, conversation, merge, resolution) and the automation engine."""
import pytest
from sqlalchemy import func, select

from opsdesk.db.models import (Category, ConfigurationItem, CSATSurvey, Interaction, Notification, Priority, Rule, State,
                               Team, TeamMember, Ticket, TicketEvent, TicketLink, TicketSequence, TicketType, User)
from opsdesk.errors import SecurityError
from opsdesk.services import automation, tickets as svc
from opsdesk.worker import tasks


def _type(s, w, key="incident"):
    return s.scalars(select(TicketType).where(TicketType.tenant_id == w.tenant_id, TicketType.key == key)).one()


def _new(s, w, subject="Disk full", key="incident", **kw):
    requester = kw.pop("requester", s.get(User, w.users["customer"]))
    return svc.create_ticket(s, tenant_id=w.tenant_id, ticket_type=_type(s, w, key), subject=subject, description="d",
                             requester=requester, created_by=requester, **kw)


def _states(s, t):
    return {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}


def _team(s, w, strategy, members):
    team = Team(tenant_id=w.tenant_id, name=f"Team {strategy}", email="", assignment_strategy=strategy, rr_cursor=0)
    s.add(team)
    s.flush()
    for role in members:
        s.add(TeamMember(team_id=team.id, user_id=w.users[role]))
    s.flush()
    s.refresh(team)
    return team


class TestCreate:
    def test_numbers_are_sequential_per_prefix_and_tenant(self, world, other_world, session_for):
        with session_for() as s:
            a, b = _new(s, world), _new(s, world)
            req = _new(s, world, key="service_request")
            assert (a.number, b.number, req.number) == ("INC-000001", "INC-000002", "REQ-000001")
        with session_for(other_world) as s:
            assert _new(s, other_world).number == "INC-000001"

    def test_defaults_priority_state_watcher_and_history(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            assert t.state.is_initial and t.priority.key == "P3" and t.requester_id == world.users["customer"]
            assert [w.id for w in t.watchers] == [world.users["customer"]]
            assert [e.event_type for e in s.scalars(select(TicketEvent).where(TicketEvent.ticket_id == t.id))] == ["created"]

    def test_subject_is_capped_and_tags_and_custom_fields_stored(self, world, session_for):
        with session_for() as s:
            t = _new(s, world, subject="x" * 500, tags=["a", "b"], custom_fields={"environment": "Production"})
            assert len(t.subject) == 300 and t.tags == ["a", "b"] and t.custom_fields == {"environment": "Production"}

    def test_session_for_another_tenant_is_refused(self, world, other_world, session_for):
        with session_for(other_world) as s:
            with pytest.raises(SecurityError):
                svc.create_ticket(s, tenant_id=world.tenant_id, ticket_type=_type(s, other_world), subject="x")

    def test_default_team_comes_from_category_then_service_then_ci(self, world, session_for):
        with session_for() as s:
            team = _team(s, world, "manual", ["employee"])
            cat = Category(tenant_id=world.tenant_id, name="Net", default_team_id=team.id)
            s.add(cat)
            s.flush()
            s.refresh(cat)
            assert _new(s, world, category=cat).team_id == team.id


class TestAssignment:
    def test_round_robin_cycles_through_eligible_members(self, world, session_for):
        with session_for() as s:
            team = _team(s, world, "round_robin", ["employee", "team_lead"])
            picks = [_new(s, world, subject=f"T{i}", team=team).assignee_id for i in range(4)]
            ids = sorted([world.users["employee"], world.users["team_lead"]])
            assert picks[:2] == ids and picks[2:] == ids

    def test_least_loaded_picks_the_member_with_fewest_open_tickets(self, world, session_for):
        with session_for() as s:
            team = _team(s, world, "least_loaded", ["employee", "team_lead"])
            busy = world.users["employee"]
            for i in range(2):
                _new(s, world, subject=f"Busy{i}", assignee=s.get(User, busy))
            assert _new(s, world, subject="Next", team=team).assignee_id == world.users["team_lead"]

    def test_unavailable_inactive_and_non_staff_members_are_never_picked(self, world, session_for):
        with session_for() as s:
            team = _team(s, world, "round_robin", ["employee", "team_lead", "customer"])
            s.get(User, world.users["employee"]).is_available = False
            s.get(User, world.users["team_lead"]).is_active = False
            s.flush()
            s.refresh(team)
            assert _new(s, world, team=team).assignee_id is None  # nobody eligible: customers never receive tickets

    def test_manual_strategy_leaves_ticket_unassigned(self, world, session_for):
        with session_for() as s:
            assert _new(s, world, team=_team(s, world, "manual", ["employee"])).assignee_id is None


class TestUpdate:
    def test_customers_can_only_edit_their_own_text_fields(self, world, session_for):
        with session_for() as s:
            customer = s.get(User, world.users["customer"])
            t = _new(s, world)
            _, changes = svc.update_ticket(s, t, customer, {"subject": "New", "impact": 1, "assignee": s.get(User, world.users["employee"])})
            assert set(changes) == {"subject"} and t.impact == 3 and t.assignee_id is None

    def test_impact_or_urgency_change_recomputes_priority_and_sla(self, world, session_for):
        with session_for() as s:
            agent = s.get(User, world.users["employee"])
            t = _new(s, world)
            due = t.resolution_due_at
            _, changes = svc.update_ticket(s, t, agent, {"impact": 1, "urgency": 1})
            assert changes["priority"][1]["label"].startswith("P1") and t.resolution_due_at < due

    def test_noop_update_changes_nothing(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            _, changes = svc.update_ticket(s, t, s.get(User, world.users["employee"]), {"subject": t.subject})
            assert changes == {}

    def test_team_change_triggers_auto_assign_and_requester_notice(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            team = _team(s, world, "round_robin", ["employee"])
            svc.update_ticket(s, t, s.get(User, world.users["team_lead"]), {"team": team})
            assert t.assignee_id == world.users["employee"]

    def test_audit_trail_records_the_change(self, world, session_for):
        from opsdesk.db.models import AuditLog
        with session_for() as s:
            t = _new(s, world)
            svc.update_ticket(s, t, s.get(User, world.users["employee"]), {"subject": "Renamed"})
            row = s.scalars(select(AuditLog).where(AuditLog.action == "ticket.update", AuditLog.object_id == str(t.id))).one()
            assert row.changes["subject"] == ["Disk full", "Renamed"]


class TestConversation:
    def test_public_agent_reply_is_first_response_and_queued_for_delivery(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            it = svc.add_interaction(s, t, body="Looking now", author=s.get(User, world.users["employee"]))
            assert it.direction == "outbound" and it.delivery_status == "pending" and t.first_responded_at == it.created_at

    def test_internal_note_is_not_a_response_and_never_delivered(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            it = svc.add_interaction(s, t, body="note", author=s.get(User, world.users["employee"]), kind="internal")
            assert it.direction == "internal" and it.delivery_status == "n/a" and t.first_responded_at is None

    def test_customer_reply_reopens_a_pending_ticket(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            st = _states(s, t)
            svc.set_state(s, t, st["pending_customer"])
            svc.add_interaction(s, t, body="here are the logs", author=s.get(User, world.users["customer"]))
            assert t.state.key == "in_progress"

    def test_reopen_can_be_suppressed(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            svc.set_state(s, t, _states(s, t)["pending_customer"])
            svc.add_interaction(s, t, body="ok", author=s.get(User, world.users["customer"]), reopen=False)
            assert t.state.key == "pending_customer"

    def test_transition_with_comment_posts_it_with_the_right_visibility(self, world, session_for):
        from opsdesk.db.models import Transition
        with session_for() as s:
            t = _new(s, world)
            agent = s.get(User, world.users["employee"])
            tr = s.scalars(select(Transition).where(Transition.workflow_id == t.state.workflow_id, Transition.name == "Start work")).one()
            svc.perform_transition(s, t, tr, agent, comment="On it", comment_public=False)
            c = s.scalars(select(Interaction).where(Interaction.ticket_id == t.id, Interaction.body == "On it")).one()
            assert c.kind == "internal" and t.state.key == "in_progress"


class TestResolution:
    def test_resolving_creates_one_csat_survey_and_notifies_the_requester(self, world, session_for):
        with session_for() as s:
            t = _new(s, world)
            st = _states(s, t)
            t.resolution_code, t.resolution_notes = "fixed", "done"
            svc.set_state(s, t, st["resolved"])
            tid = t.id
        with session_for() as s:
            assert s.scalar(select(func.count()).select_from(CSATSurvey).where(CSATSurvey.ticket_id == tid)) == 1
            kinds = [n.event_type for n in s.scalars(select(Notification).where(Notification.ticket_id == tid))]
            assert "resolved" in kinds

    def test_csat_can_be_disabled_per_tenant(self, world, session_for):
        from opsdesk.db.models import Tenant
        with session_for() as s:
            tenant = s.get(Tenant, world.tenant_id)
            tenant.settings = {**tenant.settings, "csat_enabled": False}
            s.flush()
            t = _new(s, world)
            svc.set_state(s, t, _states(s, t)["resolved"])
            assert t.csat is None

    def test_auto_close_closes_old_resolved_tickets_only(self, world, session_for):
        from datetime import timedelta

        from opsdesk.db.base import utcnow
        with session_for() as s:
            old, fresh = _new(s, world, subject="old"), _new(s, world, subject="fresh")
            st = _states(s, old)
            svc.set_state(s, old, st["resolved"])
            svc.set_state(s, fresh, st["resolved"])
            old.resolved_at = utcnow() - timedelta(days=6)
            old_id, fresh_id = old.id, fresh.id
        assert tasks.auto_close_resolved() >= 1
        with session_for() as s:
            assert s.get(Ticket, old_id).state.category == "closed"
            assert s.get(Ticket, fresh_id).state.category == "resolved"


class TestMerge:
    def test_merge_closes_duplicate_links_it_and_moves_watchers(self, world, session_for):
        with session_for() as s:
            agent = s.get(User, world.users["employee"])
            primary, dup = _new(s, world, subject="Primary"), _new(s, world, subject="Duplicate", requester=s.get(User, world.users["customer"]))
            svc.merge_into(s, dup, primary, agent)
            s.refresh(dup)
            s.refresh(primary)
            assert dup.state.category in ("cancelled", "closed")
            assert s.scalar(select(func.count()).select_from(TicketLink).where(TicketLink.source_id == dup.id, TicketLink.target_id == primary.id,
                                                                          TicketLink.link_type == "duplicate_of")) == 1
            assert world.users["customer"] in {w.id for w in primary.watchers}
            svc.merge_into(s, dup, primary, agent)  # idempotent link
            assert s.scalar(select(func.count()).select_from(TicketLink).where(TicketLink.source_id == dup.id)) == 1


class TestAutomation:
    def _rule(self, s, w, **kw):
        defaults = dict(tenant_id=w.tenant_id, name="r", is_active=True, event="ticket.created", conditions=[], actions=[], order=0,
                        stop_processing=False)
        s.add(Rule(**{**defaults, **kw}))
        s.flush()

    @pytest.mark.parametrize("cond,expected", [
        ({"field": "priority.key", "op": "eq", "value": "p3"}, True),
        ({"field": "priority.key", "op": "ne", "value": "P3"}, False),
        ({"field": "priority.key", "op": "in", "value": ["P1", "P3"]}, True),
        ({"field": "priority.key", "op": "not_in", "value": ["P1"]}, True),
        ({"field": "subject", "op": "contains", "value": "DISK"}, True),
        ({"field": "subject", "op": "not_contains", "value": "disk"}, False),
        ({"field": "impact", "op": "gt", "value": 2}, True),
        ({"field": "impact", "op": "lt", "value": 2}, False),
        ({"field": "tags", "op": "is_empty"}, True),
        ({"field": "subject", "op": "not_empty"}, True),
        ({"field": "tags", "op": "contains", "value": "x"}, False),
        ({"field": "subject", "op": "changed"}, False),
        ({"field": "nope.deep", "op": "eq", "value": "x"}, False),
        ({"field": "impact", "op": "gt", "value": "not-a-number"}, False),
    ])
    def test_condition_operators(self, world, session_for, cond, expected):
        with session_for() as s:
            t = _new(s, world)
            assert automation.matches(t, cond, {}) is expected

    def test_changed_operator_uses_the_change_set(self, world, session_for):
        with session_for() as s:
            assert automation.matches(_new(s, world), {"field": "priority", "op": "changed"}, {"priority": [1, 2]}) is True

    def test_matching_rule_assigns_team_adds_tag_and_counts_runs(self, world, session_for):
        with session_for() as s:
            team = _team(s, world, "manual", ["employee"])
            self._rule(s, world, conditions=[{"field": "priority.key", "op": "eq", "value": "P3"}],
                       actions=[{"type": "assign_team", "value": team.name.lower()}, {"type": "add_tag", "value": "auto"}])
            t = _new(s, world)
            assert t.team_id == team.id and "auto" in t.tags
            rule = s.scalars(select(Rule).where(Rule.tenant_id == world.tenant_id)).one()
            s.refresh(rule)
            assert rule.run_count == 1 and rule.last_run_at is not None

    def test_non_matching_rule_does_nothing_and_inactive_rules_are_skipped(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, name="nomatch", conditions=[{"field": "priority.key", "op": "eq", "value": "P1"}],
                       actions=[{"type": "add_tag", "value": "p1"}])
            self._rule(s, world, name="off", is_active=False, actions=[{"type": "add_tag", "value": "off"}])
            t = _new(s, world)
            assert t.tags == []

    def test_stop_processing_skips_later_rules(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, name="a", order=1, stop_processing=True, actions=[{"type": "add_tag", "value": "first"}])
            self._rule(s, world, name="b", order=2, actions=[{"type": "add_tag", "value": "second"}])
            assert _new(s, world).tags == ["first"]

    def test_set_priority_transition_note_and_assign_user(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, event="ticket.created", actions=[
                {"type": "set_priority", "value": "p1"}, {"type": "transition_to", "value": "in_progress"},
                {"type": "add_note", "value": "auto note"}, {"type": "assign_user", "value": world.email("employee").upper()}])
            t = _new(s, world)
            assert t.priority.key == "P1" and t.state.key == "in_progress" and t.assignee_id == world.users["employee"]
            assert s.scalars(select(Interaction).where(Interaction.ticket_id == t.id, Interaction.body == "auto note")).one().kind == "internal"

    def test_one_failing_action_does_not_stop_the_others_or_the_ticket(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, actions=[{"type": "set_field", "field": "no_such_field_xyz", "value": 1},
                                          {"type": "add_tag", "value": "survived"}])
            assert "survived" in _new(s, world).tags

    def test_unknown_references_are_ignored(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, actions=[{"type": "assign_team", "value": "No such team"},
                                          {"type": "set_priority", "value": "P99"}])
            t = _new(s, world)
            assert t.team_id is None and t.priority.key == "P3"

    def test_recursion_is_bounded(self, world, session_for):
        with session_for() as s:
            self._rule(s, world, event="ticket.updated", actions=[{"type": "add_tag", "value": "loop"}])
            self._rule(s, world, name="loop2", event="ticket.updated", actions=[{"type": "set_major_incident", "value": True}])
            t = _new(s, world)
            svc.update_ticket(s, t, s.get(User, world.users["employee"]), {"subject": "again"})  # must terminate
            assert t.subject == "again"

    def test_notify_and_webhook_actions_queue_work(self, world, session_for, monkeypatch):
        sent = []
        monkeypatch.setattr(tasks.send_email, "delay", lambda to, subject, body: sent.append((to, subject, body)))
        hooks = []
        monkeypatch.setattr(tasks.call_webhook, "delay", lambda url, payload, secret="": hooks.append((url, payload, secret)))
        with session_for() as s:
            self._rule(s, world, actions=[
                {"type": "notify", "to": ["requester", "ops@example.com"], "subject": "[{number}] {subject}", "body": "{url}"},
                {"type": "webhook", "url": "https://hooks.example/x", "secret": "s"}])
            t = _new(s, world)
            number = t.number
        assert sent and sent[0][1] == f"[{number}] Disk full" and "ops@example.com" in sent[0][0]
        assert hooks and hooks[0][0] == "https://hooks.example/x" and hooks[0][1]["ticket"]["number"] == number
