"""Row-level ticket visibility rules on top of tenant isolation (who may see which tickets)."""
from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..db.models import Team, TeamAccount, TeamMember, Ticket, TicketWatcher
from ..db.models.core import OWN_TICKET_ROLES, PRIVILEGED_ROLES


def visible_ticket_criteria(session: Session, user) -> ColumnElement[bool]:
    """A WHERE criterion limiting ``Ticket`` rows to those ``user`` may see. ``user`` is the ORM ``User`` row."""
    base = Ticket.tenant_id == user.tenant_id
    if user.role in PRIVILEGED_ROLES:
        return base
    watching = exists().where(TicketWatcher.ticket_id == Ticket.id, TicketWatcher.user_id == user.id)
    if user.role in OWN_TICKET_ROLES:
        return and_(base, or_(Ticket.assignee_id == user.id, Ticket.requester_id == user.id, Ticket.created_by_id == user.id))
    if user.is_agent:
        team_ids = list(session.scalars(select(TeamMember.team_id).where(TeamMember.user_id == user.id)))
        if not team_ids:
            return base
        covered = dict.fromkeys(team_ids)
        rows = session.execute(select(TeamAccount.team_id, TeamAccount.account_id)
                               .where(TeamAccount.team_id.in_(team_ids))).all()
        accounts_by_team: dict[int, set[int]] = {tid: set() for tid in covered}
        for tid, aid in rows:
            accounts_by_team[tid].add(aid)
        if any(not accs for accs in accounts_by_team.values()):  # a team covering all accounts
            return base
        acc = set().union(*accounts_by_team.values())
        return and_(base, or_(Ticket.account_id.in_(acc), Ticket.account_id.is_(None), Ticket.assignee_id == user.id))
    see_account = user.tenant.setting("customer_sees_account_tickets", True) if user.tenant_id else False
    cond = or_(Ticket.requester_id == user.id, watching)
    if see_account and user.account_id:
        cond = or_(cond, Ticket.account_id == user.account_id)
    if user.account_id:
        return and_(base, Ticket.account_id == user.account_id, cond)
    return and_(base, cond)
