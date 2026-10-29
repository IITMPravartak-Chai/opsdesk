"""Turns a normalised InboundEnvelope into a new ticket or a reply on an existing one. Channel-agnostic: every
adapter feeds this same function."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..db.base import utcnow
from ..db.models import (Account, Channel, ConfigurationItem, ContactIdentity, InboundMessage, Interaction, State,
                         Team, Ticket, TicketLink, TicketType, TicketWatcher, User)
from ..security.passwords import make_password
from ..services import commit_hooks, tickets as services
from ..services.attachments import AttachmentRejected, store_attachment
from ..db.session import tenant_session
from .base import InboundEnvelope

log = logging.getLogger(__name__)
TICKET_REF = re.compile(r"\[#?([A-Z]{2,6}-\d{3,10})\]")
DONE = ["resolved", "closed", "cancelled"]


def parse_ticket_ref(text):
    m = TICKET_REF.search(text or "")
    return m.group(1) if m else ""


def _normalise(kind, value):
    value = (value or "").strip()
    if kind == "email":
        return value.lower()
    if kind in ("whatsapp", "phone"):
        digits = re.sub(r"\D", "", value)
        return f"+{digits}" if digits else ""
    return value


def resolve_account(session: Session, channel, env, email=None):
    tid = channel.tenant_id
    code = env.hints.get("account_code") or ""
    if code:
        acc = session.scalars(select(Account).where(Account.tenant_id == tid,
                                                    func.lower(Account.code) == code.lower())).first()
        if acc:
            return acc
    if email and "@" in email:
        domain = email.rsplit("@", 1)[1].lower()
        acc = session.scalars(select(Account).where(Account.tenant_id == tid,
                                                    Account.email_domains.contains([domain]))).first()
        if acc:
            return acc
    default = channel.settings.get("account_code")
    if default:
        return session.scalars(select(Account).where(Account.tenant_id == tid,
                                                     func.lower(Account.code) == default.lower())).first()
    return None


def resolve_requester(session: Session, channel, env):
    kind = env.identity_kind
    if kind == "system" or not env.identity_value:
        return None
    value = _normalise(kind, env.identity_value)
    tid = channel.tenant_id
    ident = session.scalars(select(ContactIdentity).options(joinedload(ContactIdentity.user)).where(
        ContactIdentity.tenant_id == tid, ContactIdentity.kind == kind, ContactIdentity.value == value)).first()
    if ident:
        return ident.user
    user = None
    if kind == "email":
        user = session.scalars(select(User).where(func.lower(User.email) == value.lower())).first()
        if user and user.tenant_id != tid:
            return None  # identity belongs to another tenant: never cross-link
    elif kind in ("whatsapp", "phone"):
        user = session.scalars(select(User).where(User.tenant_id == tid, User.phone == value)).first()
    if user is None:
        email = value if kind == "email" else f"{kind}-{value.lstrip('+')}-{uuid.uuid4().hex[:6]}@contacts.invalid"
        first, _, last = (env.sender_name or "").partition(" ")
        acct = resolve_account(session, channel, env, email if kind == "email" else None)
        user = User(tenant_id=tid, email=email, username=f"{kind}-{uuid.uuid4().hex[:12]}", role="customer",
                    first_name=first[:150], last_name=last[:150], password=make_password(None),
                    phone=value if kind in ("whatsapp", "phone") else "", account_id=acct.id if acct else None)
        try:
            with commit_hooks.savepoint(session):
                session.add(user)
                session.flush()
        except IntegrityError:
            # Emails are unique across all tenants (they are the sign-in name), but RLS hides other tenants' users
            # from the lookup above, so a sender known elsewhere fails here. Reuse a same-tenant user created
            # concurrently; otherwise the address belongs to another tenant: never cross-link, carry on without a
            # requester (the sender is kept on the ticket).
            user = session.scalars(select(User).where(User.tenant_id == tid,
                                                      func.lower(User.email) == email.lower())).first()
            if user is None:
                return None
    exists = session.scalars(select(ContactIdentity.id).where(
        ContactIdentity.tenant_id == tid, ContactIdentity.kind == kind, ContactIdentity.value == value)).first()
    if exists is None:
        try:
            with commit_hooks.savepoint(session):
                session.add(ContactIdentity(tenant_id=tid, kind=kind, value=value, user_id=user.id))
                session.flush()
        except IntegrityError:
            pass
    return user


def _may_thread(session: Session, ticket, requester, account) -> bool:
    """Only let a sender reply on a ticket they are entitled to see."""
    if requester is None:
        return True
    if ticket.requester_id == requester.id:
        return True
    if session.scalars(select(TicketWatcher.id).where(TicketWatcher.ticket_id == ticket.id,
                                                      TicketWatcher.user_id == requester.id)).first():
        return True
    return bool(ticket.account_id and account and ticket.account_id == account.id)


def find_ticket(session: Session, channel, env, requester, account):
    tid = channel.tenant_id
    ref = env.ticket_ref or parse_ticket_ref(env.subject)
    if ref:
        t = session.scalars(select(Ticket).options(joinedload(Ticket.state)).where(
            Ticket.tenant_id == tid, Ticket.number == ref)).first()
        if t and _may_thread(session, t, requester, account):
            return t
    if env.in_reply_to:
        it = session.scalars(select(Interaction).where(
            Interaction.tenant_id == tid, Interaction.external_message_id.in_(env.in_reply_to)).limit(1)).first()
        if it:
            return it.ticket
        im = session.scalars(select(InboundMessage).join(Channel, Channel.id == InboundMessage.channel_id).where(
            Channel.tenant_id == tid, InboundMessage.external_id.in_(env.in_reply_to),
            InboundMessage.ticket_id.is_not(None)).limit(1)).first()
        if im:
            return im.ticket
    if env.fingerprint:
        stmt = (select(Ticket).join(State, State.id == Ticket.state_id)
                .where(Ticket.tenant_id == tid, Ticket.custom_fields["alert_fingerprint"].as_string() == env.fingerprint,
                       State.category.notin_(DONE)))
        window_hours = channel.settings.get("correlation_window_hours")
        if window_hours:
            stmt = stmt.where(Ticket.created_at >= utcnow() - timedelta(hours=int(window_hours)))
        t = session.scalars(stmt.order_by(Ticket.created_at.desc()).limit(1)).first()
        if t:
            return t

    if channel.settings.get("group_by_ci") and env.hints.get("ci"):
        ci_window = int(channel.settings.get("correlation_window_hours", 2))
        since = utcnow() - timedelta(hours=ci_window)
        t = session.scalars(
            select(Ticket).join(State, State.id == Ticket.state_id)
            .join(ConfigurationItem, ConfigurationItem.id == Ticket.ci_id)
            .where(Ticket.tenant_id == tid, func.lower(ConfigurationItem.name) == env.hints["ci"].lower(),
                   Ticket.channel_id == channel.id, Ticket.created_at >= since, State.category.notin_(DONE))
            .order_by(Ticket.created_at.desc()).limit(1)).first()
        if t:
            return t

    window = channel.settings.get("thread_window_hours")
    if window and requester is not None:
        since = utcnow() - timedelta(hours=int(window))
        t = session.scalars(
            select(Ticket).join(State, State.id == Ticket.state_id)
            .where(Ticket.tenant_id == tid, Ticket.requester_id == requester.id, Ticket.channel_id == channel.id,
                   Ticket.updated_at >= since, State.category.notin_(["closed", "cancelled"]))
            .order_by(Ticket.updated_at.desc()).limit(1)).first()
        if t:
            return t
    return None


def _acknowledge_after_commit(tenant_id: int, ticket_id: int, channel_id: int, env_dict: dict):
    """Runs after commit, when the transaction-local tenant claims are gone and forced RLS would hide the ticket's
    related rows; re-enter the ticket's tenant in a fresh session."""
    def _run() -> None:
        with tenant_session(tenant_id) as s:
            ticket, channel = s.get(Ticket, ticket_id), s.get(Channel, channel_id)
            if ticket is not None and channel is not None:
                channel.adapter.acknowledge(s, ticket, InboundEnvelope.from_dict(env_dict))
    return _run


def _save_attachments(session: Session, adapter, ticket, env, interaction=None, user=None):
    for att in env.attachments:
        try:
            data = adapter.fetch_attachment(att)
            store_attachment(session, ticket, att.get("filename"), data, att.get("content_type", ""), interaction, user)
        except AttachmentRejected as e:
            services.add_interaction(session, ticket, body=f"Attachment '{att.get('filename')}' rejected: {e}",
                                     kind="system", deliver=False)
        except Exception:  # noqa: BLE001
            log.exception("attachment fetch failed")


def _alert_stamp(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%SZ")


def ingest(session: Session, inbound: InboundMessage):
    channel = inbound.channel
    adapter = channel.adapter
    env = InboundEnvelope.from_dict(inbound.envelope)
    requester = resolve_requester(session, channel, env)
    account = (requester.account if requester and requester.account_id else None) or resolve_account(session, channel, env)
    ticket = find_ticket(session, channel, env, requester, account)

    if ticket is not None and ticket.state.is_final:
        previous, ticket = ticket, None
    else:
        previous = None

    if ticket is not None:
        body = env.body or "(no text)"
        cf = ticket.custom_fields or {}
        if env.fingerprint or cf.get("alert_fingerprint") or env.hints.get("ci"):
            n = int(cf.get("alert_occurrences", 1)) + 1
            now_str = _alert_stamp(utcnow())
            first_str = cf.get("first_alert_at") or _alert_stamp(ticket.created_at)
            alert_id = env.fingerprint or env.subject

            raw_active = cf.get("active_alerts") or []
            active_set = set(raw_active) if isinstance(raw_active, list) else set()
            if env.alert_status == "resolved":
                active_set.discard(alert_id)
            else:
                active_set.add(alert_id)

            updated_fields = {
                "alert_occurrences": n, "first_alert_at": first_str, "last_alert_at": now_str,
                "alert_status": env.alert_status or "firing", "active_alerts": sorted(active_set),
            }
            if env.fingerprint:
                updated_fields["alert_fingerprint"] = env.fingerprint
            services.update_ticket(session, ticket, None, {"custom_fields": updated_fields})

            if env.alert_status == "resolved":
                if len(active_set) > 0:
                    services.add_interaction(
                        session, ticket, kind="system", deliver=False,
                        body=f"Alert '{alert_id}' cleared at source, but {len(active_set)} other alert(s) remain "
                             f"active on this incident.\n\n{body}")
                    inbound.ticket_id, inbound.status = ticket.id, "processed"
                    session.flush()
                    return ticket

                services.add_interaction(session, ticket, body=f"Source reports all correlated alerts have cleared.\n\n{body}",
                                         kind="system", deliver=False)
                if channel.settings.get("auto_resolve", True):
                    st = session.scalars(select(State).where(State.workflow_id == ticket.state.workflow_id,
                                                             State.category == "resolved").limit(1)).first()
                    if st:
                        ticket.resolution_notes = ticket.resolution_notes or "Auto-resolved: alert cleared at source."
                        session.flush()
                        services.set_state(session, ticket, st, via="alert cleared")
                inbound.ticket_id, inbound.status = ticket.id, "processed"
                session.flush()
                return ticket
            it = services.add_interaction(session, ticket, body=f"Alert fired again (occurrence {n}).\n\n{body}",
                                          kind="system", deliver=False)
        else:
            it = services.add_interaction(session, ticket, body=body, author=requester, kind="public",
                                          source=adapter.source, channel=channel, direction="inbound",
                                          external_message_id=env.external_id)
        _save_attachments(session, adapter, ticket, env, it, requester)
    else:
        type_key = env.hints.get("ticket_type") or channel.settings.get("ticket_type", "incident")
        ttype = (session.scalars(select(TicketType).where(TicketType.tenant_id == channel.tenant_id,
                                                          TicketType.key == type_key, TicketType.is_active.is_(True))).first()
                 or session.scalars(select(TicketType).where(TicketType.tenant_id == channel.tenant_id,
                                                             TicketType.is_active.is_(True))
                                    .order_by(TicketType.order, TicketType.name).limit(1)).first())
        team = None
        if channel.settings.get("team"):
            team = session.scalars(select(Team).where(Team.tenant_id == channel.tenant_id,
                                                      func.lower(Team.name) == channel.settings["team"].lower())).first()
        ci = None
        if env.hints.get("ci"):
            ci = session.scalars(select(ConfigurationItem).where(
                ConfigurationItem.tenant_id == channel.tenant_id,
                func.lower(ConfigurationItem.name) == env.hints["ci"].lower())).first()
        custom: dict = {}
        if env.fingerprint or env.hints.get("ci"):
            now_str = _alert_stamp(utcnow())
            alert_id = env.fingerprint or env.subject
            custom = {
                "alert_fingerprint": env.fingerprint, "alert_occurrences": 1, "first_alert_at": now_str,
                "last_alert_at": now_str, "alert_status": env.alert_status or "firing",
                "active_alerts": [alert_id] if env.alert_status != "resolved" else [],
            }
        if requester is None and env.identity_value:
            custom["sender"] = env.identity_value
        subject = (env.subject or env.body.strip().split("\n")[0] or f"Message via {channel.name}")[:300]
        ticket = services.create_ticket(
            session, tenant_id=channel.tenant_id, ticket_type=ttype, subject=subject, description=env.body,
            requester=requester, account=account, source=adapter.source, channel=channel, ci=ci, team=team,
            impact=env.hints.get("impact") or channel.settings.get("impact"),
            urgency=env.hints.get("urgency") or channel.settings.get("urgency"),
            tags=list(env.hints.get("tags", [])), custom_fields=custom,
        )
        if previous is not None:
            exists = session.scalars(select(TicketLink.id).where(
                TicketLink.source_id == ticket.id, TicketLink.target_id == previous.id,
                TicketLink.link_type == "related")).first()
            if exists is None:
                session.add(TicketLink(tenant_id=ticket.tenant_id, source_id=ticket.id, target_id=previous.id,
                                       link_type="related"))
            services.add_interaction(session, ticket, body=f"Follow-up to closed ticket {previous.number}.",
                                     kind="system", deliver=False)
        _save_attachments(session, adapter, ticket, env, None, requester)
        commit_hooks.on_commit(session, _acknowledge_after_commit(ticket.tenant_id, ticket.id, channel.id, env.to_dict()),
                               robust=True)

    inbound.ticket_id, inbound.status = ticket.id, "processed"
    session.flush()
    return ticket
