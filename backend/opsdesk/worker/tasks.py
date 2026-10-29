"""Celery tasks.

Each task opens its own tenant-bound session (``tenant_session``); cross-tenant jobs iterate active tenants.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import requests
from celery import shared_task
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .celery_app import app  # noqa: F401  (activates the configured Celery app before tasks register)
from ..channels.base import InboundEnvelope, ParseResult
from ..db.base import utcnow
from ..db.models import (AuthBlacklistedToken, Category, Channel, InboundMessage, Interaction, KnowledgeArticle,
                         State, Team, Tenant, Ticket)
from ..db.session import plain_session, tenant_session
from ..errors import OpsDeskError
from ..services import commit_hooks, notifications, sla
from ..services import mailer
from ..services import tickets as services
from ..settings import settings

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- shared helpers
MAX_ATTEMPTS = settings.inbound_max_attempts
DISPATCH_GRACE = timedelta(seconds=120)
PROCESSING_LEASE = timedelta(seconds=300)
RECONCILE_BATCH = 100
PERMANENT_ERRORS = (ValueError, KeyError, TypeError, LookupError)


def tenant_ids() -> list[int]:
    with plain_session() as s:
        return list(s.scalars(select(Tenant.id).where(Tenant.is_active.is_(True)).order_by(Tenant.id)))


def retry_delay(attempts: int) -> timedelta:
    """Backoff after the given number of failed attempts: 1, 2, 4, 8 ... minutes, capped at an hour."""
    return timedelta(seconds=min(60 * 2 ** max(attempts - 1, 0), 3600))


def classify(exc: Exception) -> str:
    return "permanent" if isinstance(exc, PERMANENT_ERRORS) else "transient"


# --------------------------------------------------------------------------- email / webhooks
@shared_task(name="opsdesk.tickets.tasks.send_email", autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_email(recipients, subject, body):
    mailer.send_mail(subject, body, list(recipients))


@shared_task(name="opsdesk.automation.tasks.call_webhook", autoretry_for=(requests.RequestException,),
             retry_backoff=True, max_retries=6)
def call_webhook(url, payload, secret=""):
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "OpsDesk-Webhook/1.0"}
    if secret:
        headers["X-OpsDesk-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = requests.post(url, data=body, headers=headers, timeout=10)
    r.raise_for_status()
    return r.status_code


# --------------------------------------------------------------------------- SLA / housekeeping
@shared_task(name="opsdesk.tickets.tasks.sla_monitor")
def sla_monitor():
    now = utcnow()
    changed = 0
    for tid in tenant_ids():
        with tenant_session(tid) as s:
            ids = list(s.scalars(select(Ticket.id).join(State, State.id == Ticket.state_id)
                                 .where(Ticket.tenant_id == tid, sla.active_sla_clause())))
            for ticket_id in ids:
                t = s.get(Ticket, ticket_id)
                before = (t.sla_status, t.response_breached, t.resolution_breached)
                status = sla.evaluate(t, now)
                if (status, t.response_breached, t.resolution_breached) == before:
                    continue
                t.sla_status = status
                s.flush()
                changed += 1
                if status != before[0] and status in ("at_risk", "breached"):
                    services.log_event(s, t, f"sla_{status}")
                    notifications.notify_sla(s, t, status)
                    services.emit(s, t, f"sla.{status}")
    return changed


@shared_task(name="opsdesk.tickets.tasks.auto_close_resolved")
def auto_close_resolved():
    closed = 0
    for tid in tenant_ids():
        with tenant_session(tid) as s:
            tenant = s.get(Tenant, tid)
            days = int(tenant.setting("auto_close_days", 5))
            if days <= 0:
                continue
            cutoff = utcnow() - timedelta(days=days)
            rows = s.scalars(select(Ticket).join(State, State.id == Ticket.state_id).where(
                Ticket.tenant_id == tid, State.category == "resolved", Ticket.resolved_at < cutoff)).all()
            for t in rows:
                target = s.scalars(select(State).where(State.workflow_id == t.state.workflow_id,
                                                       State.category == "closed").limit(1)).first()
                if target:
                    services.set_state(s, t, target, actor=None, via=f"auto-close after {days} days")
                    closed += 1
    return closed


@shared_task(name="opsdesk.auth.purge_revoked_tokens")
def purge_revoked_tokens():
    with plain_session() as s:
        return s.execute(delete(AuthBlacklistedToken).where(AuthBlacklistedToken.expires_at < utcnow())).rowcount


# --------------------------------------------------------------------------- AI
@shared_task(name="opsdesk.ai.tasks.enrich_ticket", bind=True, max_retries=3, default_retry_delay=20)
def enrich_ticket(self, tenant_id, ticket_id):
    """Embed, detect duplicates, and compute triage suggestions for a ticket."""
    from ..ai import engine

    with tenant_session(tenant_id) as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return
        try:
            engine.embed_ticket(s, t)
        except Exception as e:  # noqa: BLE001
            log.exception("embedding failed ticket=%s", ticket_id)
            raise self.retry(exc=e)
        dups = engine.detect_duplicates(s, t)
        suggestion = engine.triage(s, t) if not t.state.is_done else {}
        t.ai_triage = {**suggestion, "possible_duplicates": dups}
        s.flush()
        if dups:
            services.log_event(s, t, "ai_possible_duplicate", None, candidates=[d["number"] for d in dups])
        tenant = t.tenant
        threshold = float(tenant.setting("triage_confidence_threshold", 0.8))
        if tenant.setting("auto_apply_triage", False) and suggestion.get("confidence", 0) >= threshold:
            changes = {}
            if not t.category_id and suggestion.get("category_id"):
                changes["category"] = s.get(Category, suggestion["category_id"])
            if not t.team_id and suggestion.get("team_id"):
                changes["team"] = s.get(Team, suggestion["team_id"])
            changes = {k: v for k, v in changes.items() if v is not None}
            if changes:
                s.refresh(t)
                services.update_ticket(s, t, None, changes)
                services.log_event(s, t, "ai_triage_applied", None, confidence=suggestion.get("confidence"))


@shared_task(name="opsdesk.ai.tasks.embed_article")
def embed_article(tenant_id, article_id):
    from ..ai import engine

    with tenant_session(tenant_id) as s:
        a = s.get(KnowledgeArticle, article_id)
        if a:
            engine.embed_article(s, a)


# --------------------------------------------------------------------------- channels: durable inbound outbox
def dispatch(session, tenant_id: int, inbound_id: int) -> None:
    """Queue processing after the surrounding transaction commits; a broker failure leaves the row due."""
    commit_hooks.on_commit(session, lambda: process_inbound.delay(tenant_id, inbound_id), robust=True)


def accept(session, channel, result: ParseResult) -> int:
    """Persist envelopes (idempotently) and queue them for processing. ``session`` is tenant-bound."""
    accepted = 0
    for env in result.envelopes:
        row = session.execute(
            pg_insert(InboundMessage).values(
                tenant_id=channel.tenant_id, channel_id=channel.id, external_id=env.external_id[:255],
                envelope=env.to_dict(), status="queued", error="", attempts=0, received_at=utcnow(),
                next_attempt_at=utcnow() + DISPATCH_GRACE)
            .on_conflict_do_nothing(index_elements=["channel_id", "external_id"])
            .returning(InboundMessage.id)).first()
        if row:  # a duplicate delivery inserts nothing
            accepted += 1
            # Queue only once the row is committed: a worker that runs first cannot see it (U6 E4).
            dispatch(session, channel.tenant_id, row[0])
    for ext_id, status in result.delivery_updates:
        session.execute(update(Interaction).where(Interaction.tenant_id == channel.tenant_id,
                                                  Interaction.external_message_id == ext_id)
                        .values(delivery_status=status))
    return accepted


@shared_task(name="opsdesk.channels.tasks.process_inbound")
def process_inbound(tenant_id, inbound_id):
    from ..channels.router import ingest

    # Phase 1, committed on its own: claim one attempt and set a lease, so a worker that dies mid-processing (or a
    # database that drops the connection) still leaves a counted attempt and a due retry behind.
    with tenant_session(tenant_id) as s:
        msg = s.scalars(select(InboundMessage).where(InboundMessage.id == inbound_id).with_for_update()).first()
        if msg is None or msg.status != "queued":
            return msg.ticket_id if msg else None
        if msg.attempts >= MAX_ATTEMPTS:
            msg.status, msg.next_attempt_at = "failed", None
            msg.error = (msg.error or "transient:unknown") + ";exhausted"
            log.warning("inbound message failed permanently id=%s attempts=%s", inbound_id, msg.attempts)
            return None
        now = utcnow()
        msg.attempts, msg.last_attempt_at, msg.next_attempt_at = msg.attempts + 1, now, now + PROCESSING_LEASE

    # Phase 2: the business work runs in a savepoint under the row lock. The lock plus the status re-check stop a
    # duplicate dispatch from ingesting twice; a failure rolls back only the savepoint, so the failure record
    # below commits instead of vanishing with it.
    with tenant_session(tenant_id) as s:
        msg = s.scalars(select(InboundMessage).where(InboundMessage.id == inbound_id).with_for_update()).first()
        if msg is None or msg.status != "queued":
            return msg.ticket_id if msg else None
        attempts = msg.attempts
        try:
            with commit_hooks.savepoint(s):
                ticket = ingest(s, msg)
        except Exception as exc:  # noqa: BLE001
            kind = classify(exc)
            retry = kind == "transient" and attempts < MAX_ATTEMPTS
            try:
                s.execute(update(InboundMessage).where(InboundMessage.id == inbound_id).values(
                    status="queued" if retry else "failed", ticket_id=None, error=f"{kind}:{type(exc).__name__}",
                    next_attempt_at=utcnow() + retry_delay(attempts) if retry else None))
            except Exception:  # noqa: BLE001
                # e.g. the connection itself is gone. Phase 1 already committed the attempt and a lease, so the
                # reconciler retries this message when the lease expires; surface the original error.
                log.exception("could not record inbound failure id=%s (original %s)", inbound_id, type(exc).__name__)
                raise exc
            log.warning("inbound processing failed id=%s attempt=%s/%s class=%s:%s retry=%s",
                        inbound_id, attempts, MAX_ATTEMPTS, kind, type(exc).__name__, retry)
            return None
        s.execute(update(InboundMessage).where(InboundMessage.id == inbound_id).values(next_attempt_at=None, error=""))
        return ticket.id if ticket else None


@shared_task(name="opsdesk.channels.tasks.reconcile_inbound")
def reconcile_inbound():
    """Re-dispatch queued messages whose deadline passed (lost dispatch, broker outage, dead worker, backoff
    elapsed). SKIP LOCKED keeps concurrent reconcilers and in-flight workers from fighting over a row; a duplicate
    dispatch is harmless because process_inbound re-checks status under the row lock."""
    due_total = 0
    now = utcnow()
    for tid in tenant_ids():
        with tenant_session(tid) as s:
            due = s.scalars(
                select(InboundMessage).where(
                    InboundMessage.status == "queued",
                    or_(InboundMessage.next_attempt_at <= now,
                        (InboundMessage.next_attempt_at.is_(None)) & (InboundMessage.received_at <= now - DISPATCH_GRACE)))
                .order_by(InboundMessage.received_at).limit(RECONCILE_BATCH).with_for_update(skip_locked=True)).all()
            for msg in due:
                msg.next_attempt_at = now + PROCESSING_LEASE  # don't re-dispatch it again on the next run
                dispatch(s, tid, msg.id)
                due_total += 1
    if due_total:
        log.info("reconcile_inbound re-dispatched %s message(s)", due_total)
    return due_total


@shared_task(name="opsdesk.channels.tasks.deliver_interaction", bind=True, max_retries=4, default_retry_delay=60)
def deliver_interaction(self, tenant_id, interaction_id):
    with tenant_session(tenant_id) as s:
        it = s.get(Interaction, interaction_id)
        if it is None:
            return None
        ticket = it.ticket
        channel = ticket.channel
        try:
            if channel and channel.is_active and channel.adapter.supports_outbound:
                ext = channel.adapter.send(s, ticket, it)
                it.external_message_id, it.channel_id, it.source = ext or "", channel.id, channel.adapter.source
            else:
                notifications.notify_portal_reply(s, ticket, it)
            it.delivery_status, it.delivery_error = "sent", ""
        except Exception as e:  # noqa: BLE001
            it.delivery_status, it.delivery_error = "failed", str(e)[:1000]
            s.flush()
            if self.request.called_directly or self.request.is_eager:
                return "failed"
            s.commit()  # persist the failure record before asking Celery to retry
            raise self.retry(exc=e)
        s.flush()
        return it.delivery_status


@shared_task(name="opsdesk.channels.tasks.poll_pull_channels")
def poll_pull_channels():
    from ..channels.registry import adapter_classes

    pull_kinds = [k for k, cls in adapter_classes().items() if cls.is_pull]
    total = 0
    for tid in tenant_ids():
        with tenant_session(tid) as s:
            for ch in s.scalars(select(Channel).where(Channel.is_active.is_(True), Channel.kind.in_(pull_kinds))).all():
                try:
                    total += accept(s, ch, ch.adapter.poll(s))
                except Exception:  # noqa: BLE001
                    log.exception("poll failed channel=%s", ch.id)
    return total
