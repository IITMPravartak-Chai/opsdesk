"""Email and in-app notifications to agents and portal requesters. Channel replies (email threads,
WhatsApp) are delivered by the channel adapters instead."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Notification, NotificationPreference, User
from ..db.models.core import PRIVILEGED_ROLES
from ..settings import settings
from . import commit_hooks

log = logging.getLogger(__name__)


def send(session: Session, recipients, subject: str, body: str) -> None:
    """Queue an email once the surrounding change has committed."""
    recipients = sorted({r for r in recipients if r})
    if not recipients:
        return

    def _queue() -> None:
        from ..worker.tasks import send_email
        send_email.delay(recipients, subject, body)

    # robust: the change that triggered the email has already committed; an unreachable broker is logged
    # instead of turning that committed change into an error response.
    commit_hooks.on_commit(session, _queue, robust=True)


def ticket_url(ticket) -> str:
    return f"{settings.public_base_url}/tickets/{ticket.id}"


def _log_failure(level, step, exc, ticket, recipient, event_type) -> None:
    # Ids and the exception class only: never the title or body, and not str(exc), whose database detail can
    # echo the row's values.
    log.log(level, "in-app notification %s failed: tenant_id=%s recipient_id=%s ticket_id=%s event_type=%s error=%s",
            step, getattr(recipient, "tenant_id", None), getattr(recipient, "id", None),
            getattr(ticket, "id", None), (event_type or "")[:60], type(exc).__name__)


def create_in_app_notification(session: Session, ticket, recipient, event_type, title, body="", actor=None):
    """Create an in-app notification respecting user preferences and tenant isolation.

    Delivery is secondary to the business operation that triggers it: a failure is logged and returns None.
    Each database step runs in its own savepoint so a failure here can never leave the caller's transaction
    aborted."""
    if not recipient or not getattr(recipient, "is_active", True):
        return None
    if actor and actor.id == recipient.id:  # do not notify users of their own actions
        return None

    try:
        with commit_hooks.savepoint(session):
            prefs = session.scalars(select(NotificationPreference).where(
                NotificationPreference.user_id == recipient.id)).first()
    except Exception as exc:  # noqa: BLE001 - established behaviour: unreadable preferences mean defaults apply
        _log_failure(logging.WARNING, "preference lookup", exc, ticket, recipient, event_type)
        prefs = None

    if prefs and not prefs.in_app_enabled:
        return None
    if prefs:
        if event_type in ("assigned", "unassigned") and not prefs.notify_on_assignment:
            return None
        if event_type in ("reply", "resolved", "status_changed") and not prefs.notify_on_replies:
            return None
        if event_type in ("approval_requested", "approval_granted", "approval_rejected") and not prefs.notify_on_approvals:
            return None
        if event_type.startswith("sla") and not prefs.notify_on_sla:
            return None

    tenant_id = getattr(ticket, "tenant_id", None) or getattr(recipient, "tenant_id", None)
    if not tenant_id:
        return None

    try:
        with commit_hooks.savepoint(session):
            n = Notification(tenant_id=tenant_id, recipient_id=recipient.id, actor_id=getattr(actor, "id", None),
                             event_type=event_type, title=(title or "")[:200], body=(body or "")[:500],
                             ticket_id=getattr(ticket, "id", None))
            session.add(n)
            session.flush()
            return n
    except Exception as exc:  # noqa: BLE001
        _log_failure(logging.ERROR, "create", exc, ticket, recipient, event_type)
        return None


def team_supervisors(ticket) -> list:
    """Who oversees the ticket's team: its named team lead and manager, plus any member holding a lead or manager
    role (so a team without named supervisors still has someone to alert)."""
    team = ticket.team
    if team is None:
        return []
    people = [team.team_lead, team.manager, *(m for m in team.members if m.role in PRIVILEGED_ROLES)]
    return list({u.id: u for u in people if u is not None}.values())


def _notify_each(session: Session, ticket, recipients, event_type, title, body="", actor=None, skip_ids=()) -> None:
    for recipient in {u.id: u for u in recipients if u is not None and u.id not in skip_ids}.values():
        create_in_app_notification(session, ticket, recipient, event_type, title, body, actor=actor)


def notify_unassigned(session: Session, ticket, actor=None) -> None:
    """A ticket sits in a team with nobody assigned (new, transferred in, or its assignee was removed)."""
    if ticket.assignee_id or ticket.team is None:
        return
    _notify_each(session, ticket, team_supervisors(ticket), "unassigned",
                 f"Unassigned ticket {ticket.number} in {ticket.team.name}", f"{ticket.subject}", actor=actor)


def notify_major_incident(session: Session, ticket, actor=None) -> None:
    """Every lead and manager of the tenant, the team's supervisors, the assignee and the incident commander."""
    leaders = session.scalars(select(User).where(User.tenant_id == ticket.tenant_id, User.role.in_(PRIVILEGED_ROLES),
                                                 User.is_active.is_(True))).all()
    _notify_each(session, ticket, [*leaders, *team_supervisors(ticket), ticket.assignee, ticket.incident_commander],
                 "major_incident", f"Major incident declared: {ticket.number}", f"{ticket.subject}", actor=actor)


def notify_approval_requested(session: Session, ticket, approver, actor=None) -> None:
    create_in_app_notification(session, ticket, approver, "approval_requested",
                               f"Approval requested on {ticket.number}", f"{ticket.subject}", actor=actor)


def notify_approval_decided(session: Session, ticket, approval, actor=None) -> None:
    """Tell whoever asked for the approval, the requester and the assignee how it was decided."""
    granted = approval.status == "approved"
    _notify_each(session, ticket, [approval.requested_by, ticket.requester, ticket.assignee],
                 "approval_granted" if granted else "approval_rejected",
                 f"Approval {'granted' if granted else 'rejected'} on {ticket.number}",
                 (approval.comment or ticket.subject or "")[:200], actor=actor)


def notify_assignment(session: Session, ticket, actor=None) -> None:
    if ticket.assignee and ticket.assignee.email:
        send(session, [ticket.assignee.email], f"[{ticket.number}] Assigned to you: {ticket.subject}",
             f"{ticket.number} ({ticket.priority.key if ticket.priority else '-'}) is now assigned to you.\n\n"
             f"{ticket.subject}\n\n{ticket_url(ticket)}")
    if ticket.assignee:
        create_in_app_notification(session, ticket, ticket.assignee, "assigned",
                                   f"Ticket {ticket.number} assigned to you", f"{ticket.subject}", actor=actor)


def notify_ticket_created(session: Session, ticket) -> None:
    # Requesters created via the portal get an acknowledgement; channel-originated tickets get theirs through
    # the same channel (see channels.router).
    if ticket.source in ("portal", "mobile") and ticket.requester and not ticket.requester.is_agent:
        send(session, [ticket.requester.email], f"[{ticket.number}] We received your request: {ticket.subject}",
             f"Thanks, we've logged your request as {ticket.number}. Track it here: {ticket_url(ticket)}")
    if ticket.team and ticket.team.email and not ticket.assignee_id:
        send(session, [ticket.team.email], f"[{ticket.number}] New {ticket.ticket_type.name.lower()}: {ticket.subject}",
             f"Priority {ticket.priority.key if ticket.priority else '-'} for "
             f"{ticket.account.name if ticket.account else 'unknown account'}.\n{ticket_url(ticket)}")


def notify_interaction(session: Session, ticket, interaction, author=None) -> None:
    """In-app notification for a new reply or internal note, whatever its direction or channel (portal, mobile,
    email, WhatsApp). Runs in the transaction that adds the interaction, so a rollback leaves nothing behind.
    The author is never notified of their own message (see create_in_app_notification)."""
    body = (interaction.body or "")[:200]
    if interaction.kind == "internal":
        # Internal notes must NEVER be disclosed to requesters: only the assignee hears about them. A note without
        # an author is written by an automation rule and is not worth an alert.
        if author is not None and ticket.assignee:
            create_in_app_notification(session, ticket, ticket.assignee, "reply", f"Internal note on {ticket.number}",
                                       body, actor=author)
        return
    if interaction.kind != "public":
        return
    recipients = {u.id: u for u in (ticket.assignee, ticket.requester) if u is not None}
    for recipient in recipients.values():
        create_in_app_notification(session, ticket, recipient, "reply", f"New reply on {ticket.number}", body,
                                   actor=author)


def notify_portal_reply(session: Session, ticket, interaction) -> None:
    """Email a public staff reply to the requester of a ticket that has no outbound channel. The in-app
    notification is created by notify_interaction when the reply is added."""
    if interaction.kind != "public":  # internal notes must NEVER be disclosed to requesters
        return
    if ticket.requester and ticket.requester.email:
        send(session, [ticket.requester.email], f"[{ticket.number}] New reply: {ticket.subject}",
             f"{interaction.body}\n\n---\nView or reply: {ticket_url(ticket)}")


def notify_resolved(session: Session, ticket, survey=None, actor=None) -> None:
    if ticket.requester and ticket.requester.email and ticket.source not in ("whatsapp",):
        body = f"Your request {ticket.number} has been resolved.\n\n{ticket.resolution_notes or ''}\n"
        if survey:
            body += f"\nHow did we do? Rate us (takes 10 seconds): {settings.public_base_url}/csat/{survey.token}\n"
        body += "\nIf the issue persists, just reply to this message and we'll reopen it."
        send(session, [ticket.requester.email], f"[{ticket.number}] Resolved: {ticket.subject}", body)

    if ticket.requester:
        create_in_app_notification(session, ticket, ticket.requester, "resolved", f"Ticket {ticket.number} resolved",
                                   f"{ticket.subject}", actor=actor)


def notify_sla(session: Session, ticket, level: str) -> None:
    to = []
    if ticket.assignee:
        to.append(ticket.assignee.email)
    if ticket.team and ticket.team.email:
        to.append(ticket.team.email)
    word = "is at risk of breaching" if level == "at_risk" else "has BREACHED"
    due = ticket.resolution_due_at.strftime("%Y-%m-%d %H:%M UTC") if ticket.resolution_due_at else "-"
    send(session, to, f"[{ticket.number}] SLA {level.replace('_', ' ')}: {ticket.subject}",
         f"The SLA for {ticket.number} {word}. Resolution due {due}.\n{ticket_url(ticket)}")
    # The assignee, and the people who oversee the ticket's team.
    _notify_each(session, ticket, [ticket.assignee, *team_supervisors(ticket)], f"sla_{level}",
                 f"SLA {level.replace('_', ' ')}: {ticket.number}", f"SLA {word} for {ticket.subject}")


def notify_team_transfer(session: Session, ticket, old_team, new_team) -> None:
    if not (ticket.requester and ticket.requester.email):
        return
    old_name = old_team.name if hasattr(old_team, "name") else str(old_team)
    new_name = new_team.name if hasattr(new_team, "name") else str(new_team)
    send(session, [ticket.requester.email], f"[{ticket.number}] Ticket transferred: {ticket.subject}",
         f"Your ticket {ticket.number} has been transferred from {old_name} to {new_name}.\n\n"
         f"{ticket.subject}\n\nTrack it here: {ticket_url(ticket)}")
