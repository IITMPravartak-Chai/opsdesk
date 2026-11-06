"""Evaluates no-code automation rules (configured in the Administration console)."""
from __future__ import annotations

import logging
from contextvars import ContextVar

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..db.models import Priority, Rule, State, Team, User
from ..db.models.core import STAFF_ROLES
from . import commit_hooks

log = logging.getLogger(__name__)
_depth: ContextVar = ContextVar("automation_depth", default=0)
MAX_DEPTH = 2


def resolve(ticket, path):
    """Dotted path on the ticket, e.g. 'priority.key', 'ci.ci_type', 'custom_fields.region'."""
    cur = ticket
    for part in path.split("."):
        if cur is None:
            return None
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
    return cur


def _norm(v):
    return v.lower() if isinstance(v, str) else v


def matches(ticket, cond, changes) -> bool:
    field, op, expected = cond.get("field", ""), cond.get("op", "eq"), cond.get("value")
    if op == "changed":
        return field.split(".")[0] in changes
    actual = resolve(ticket, field)
    if hasattr(actual, "id") and not isinstance(actual, (str, int, float, bool, dict, list)):
        actual = str(actual.id)
    a, e = _norm(actual), _norm(expected)
    try:
        if op == "eq":
            return a == e
        if op == "ne":
            return a != e
        if op == "in":
            return a in [_norm(x) for x in (expected or [])]
        if op == "not_in":
            return a not in [_norm(x) for x in (expected or [])]
        if op == "contains":
            if isinstance(actual, (list, tuple)):
                return e in [_norm(x) for x in actual]
            return e in (a or "")
        if op == "not_contains":
            return not matches(ticket, {**cond, "op": "contains"}, changes)
        if op == "gt":
            return a is not None and float(a) > float(e)
        if op == "lt":
            return a is not None and float(a) < float(e)
        if op == "is_empty":
            return a in (None, "", [], {})
        if op == "not_empty":
            return a not in (None, "", [], {})
    except (TypeError, ValueError):
        return False
    return False


def run_rules(session: Session, ticket, event: str, actor=None, changes=None) -> list[str]:
    from . import tickets as services
    depth = _depth.get()
    if depth >= MAX_DEPTH:
        return []
    token = _depth.set(depth + 1)
    fired: list[str] = []
    try:
        rules = session.scalars(select(Rule).where(Rule.tenant_id == ticket.tenant_id, Rule.event == event,
                                                   Rule.is_active.is_(True)).order_by(Rule.order, Rule.id)).all()
        for rule in rules:
            if all(matches(ticket, c, changes or {}) for c in (rule.conditions or [])):
                for action in rule.actions or []:
                    try:
                        with commit_hooks.savepoint(session):
                            apply_action(session, ticket, action, rule)
                    except Exception:  # one bad action must not break ticket processing
                        log.exception("automation action failed rule=%s", rule.id)
                session.execute(update(Rule).where(Rule.id == rule.id)
                                .values(run_count=Rule.run_count + 1, last_run_at=utcnow()))
                fired.append(rule.name)
                if rule.stop_processing:
                    break
        if fired:
            services.log_event(session, ticket, "automation", None, event=event, rules=fired)
    finally:
        _depth.reset(token)
    return fired


def apply_action(session: Session, ticket, action: dict, rule) -> None:
    from . import notifications
    from . import tickets as services

    kind, value = action.get("type"), action.get("value")
    tid = ticket.tenant_id
    if kind == "assign_team":
        team = session.scalars(select(Team).where(Team.tenant_id == tid, func.lower(Team.name) == str(value).lower())).first()
        if team:
            services.update_ticket(session, ticket, None, {"team": team})
    elif kind == "assign_user":
        user = session.scalars(select(User).where(User.tenant_id == tid, func.lower(User.email) == str(value).lower(),
                                                  User.role.in_(STAFF_ROLES))).first()
        if user:
            services.update_ticket(session, ticket, None, {"assignee": user})
    elif kind == "set_priority":
        p = session.scalars(select(Priority).where(Priority.tenant_id == tid, func.lower(Priority.key) == str(value).lower())).first()
        if p:
            services.update_ticket(session, ticket, None, {"priority": p})
    elif kind == "add_tag":
        if value and value not in (ticket.tags or []):
            services.update_ticket(session, ticket, None, {"tags": [*(ticket.tags or []), value]})
    elif kind == "set_major_incident":
        services.update_ticket(session, ticket, None, {"is_major_incident": bool(value)})
    elif kind == "set_field":
        services.update_ticket(session, ticket, None, {action["field"]: value})
    elif kind == "transition_to":
        st = session.scalars(select(State).where(State.workflow_id == ticket.state.workflow_id, State.key == value)).first()
        if st:
            services.set_state(session, ticket, st, actor=None, via=f"rule: {rule.name}")
    elif kind == "add_note":
        services.add_interaction(session, ticket, body=str(value), kind="internal", deliver=False)
    elif kind == "notify":
        to: list[str] = []
        for r in action.get("to", []):
            if r == "assignee" and ticket.assignee:
                to.append(ticket.assignee.email)
            elif r == "team" and ticket.team:
                to.extend([ticket.team.email] if ticket.team.email else [m.email for m in ticket.team.members])
            elif r == "requester" and ticket.requester:
                to.append(ticket.requester.email)
            elif "@" in r:
                to.append(r)
        subject = action.get("subject", "[{number}] {subject}").format(number=ticket.number, subject=ticket.subject)
        body = action.get("body", "{url}").format(number=ticket.number, subject=ticket.subject,
                                                  url=notifications.ticket_url(ticket))
        notifications.send(session, to, subject, body)
    elif kind == "webhook":
        payload = {"rule": rule.name, "ticket": {
            "id": ticket.id, "number": ticket.number, "subject": ticket.subject,
            "priority": ticket.priority.key if ticket.priority else None, "state": ticket.state.key,
            "account": ticket.account.code if ticket.account else None, "sla_status": ticket.sla_status}}
        url, secret = action["url"], action.get("secret", "")

        def _fire() -> None:
            from ..worker.tasks import call_webhook
            call_webhook.delay(url, payload, secret)

        commit_hooks.on_commit(session, _fire)
