"""Workflow transition rules (who may move a ticket where, and what must be present first)."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..db.models import Transition
from ..db.models.core import effective_roles


class TransitionError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def _role_ok(transition, user) -> bool:
    if user is None:  # system / automation
        return True
    if not user.is_agent:
        return transition.customer_allowed
    return not transition.allowed_roles or any(r in transition.allowed_roles for r in effective_roles(user.role))


def available_transitions(session: Session, ticket, user=None) -> list:
    st = ticket.state
    stmt = (select(Transition).options(joinedload(Transition.to_state))
            .where(Transition.workflow_id == st.workflow_id,
                   or_(Transition.from_state_id == st.id, Transition.from_state_id.is_(None)),
                   Transition.to_state_id != st.id)
            .order_by(Transition.order, Transition.id))
    out = []
    for t in session.scalars(stmt).unique():
        if t.from_state_id is None and st.is_final:
            continue
        if _role_ok(t, user):
            out.append(t)
    return out


def _field_value(ticket, name):
    if name != "custom_fields" and hasattr(ticket, name):
        val = getattr(ticket, name)
        return getattr(val, "id", val)
    return (ticket.custom_fields or {}).get(name)


def validate_transition(ticket, transition, user=None, comment="") -> None:
    if transition.workflow_id != ticket.state.workflow_id:
        raise TransitionError("Transition belongs to a different workflow")
    if transition.from_state_id and transition.from_state_id != ticket.state_id:
        raise TransitionError(f"'{transition.name}' is not available from status {ticket.state.name}")
    if transition.from_state_id is None and ticket.state.is_final:
        raise TransitionError("Ticket is closed")
    if not _role_ok(transition, user):
        raise TransitionError("You are not allowed to perform this transition")
    if transition.requires_comment and not (comment or "").strip():
        raise TransitionError("A comment is required", field="comment")
    for f in transition.required_fields or []:
        v = _field_value(ticket, f)
        if v in (None, "", [], {}):
            raise TransitionError(f"'{f}' is required for '{transition.name}'", field=f)
    if transition.requires_approval:
        approvals = list(ticket.approvals)
        if not approvals:
            raise TransitionError("Approval required: request approval first")
        if any(a.status != "approved" for a in approvals):
            raise TransitionError("All approvals must be approved first")
