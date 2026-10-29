"""All ticket mutations go through this module so that SLA, history, audit, automation, AI enrichment and
notifications behave identically whether a change comes from the portal, the mobile app, email, WhatsApp or a
monitoring alert.

Every function takes the caller's tenant-bound ``Session`` (opened with ``tenant_session``); the caller owns
the transaction. Side effects that must only happen if the change commits are registered with
``commit_hooks.on_commit``."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import tenancy
from ..db.base import utcnow
from ..db.models import (Approval, CSATSurvey, Interaction, Priority, PriorityMatrix, State, Ticket, TicketEvent,
                         TicketLink, TicketSequence, TicketWatcher, Transition, User)
from ..db.models.core import PRIVILEGED_ROLES, STAFF_ROLES
from ..db.session import TENANT_KEY, tenant_session
from ..errors import ValidationFailed
from . import automation, commit_hooks, notifications, sla
from .audit import audit
from .workflow import TransitionError, validate_transition

AGENT_EDITABLE = {"subject", "description", "account", "assignee", "team", "category", "service", "ci",
                  "impact", "urgency", "priority", "tags", "custom_fields", "is_major_incident", "parent",
                  "resolution_code", "resolution_notes"}
CUSTOMER_EDITABLE = {"subject", "description", "custom_fields"}
DONE_CATEGORIES = ("resolved", "closed", "cancelled")


def assert_session_tenant(session: Session, tenant_id: int) -> None:
    """Refuse to touch another tenant's data through a session bound to a different tenant."""
    bound = session.info.get(TENANT_KEY)
    if bound is None or int(bound) != int(tenant_id):
        raise tenancy.SecurityError("Session tenant context does not match the requested tenant.")


def _jsonable(v):
    if v is not None and hasattr(v, "__table__"):
        return {"id": v.id, "label": str(v)}
    if isinstance(v, (list, dict, str, int, float, bool)) or v is None:
        return v
    return str(v)


def _ident(v):
    return getattr(v, "id", v) if (v is not None and hasattr(v, "__table__")) else v


def log_event(session: Session, ticket, event_type: str, actor=None, **data) -> TicketEvent:
    ev = TicketEvent(tenant_id=ticket.tenant_id, ticket_id=ticket.id, actor_id=getattr(actor, "id", None),
                     event_type=event_type, data=data)
    session.add(ev)
    return ev


def emit(session: Session, ticket, event: str, actor=None, changes=None) -> None:
    automation.run_rules(session, ticket, event, actor=actor, changes=changes or {})


def next_number(session: Session, tenant_id: int, prefix: str) -> str:
    """Gap-free per-tenant, per-prefix numbering. The sequence row is locked until the transaction ends."""
    stmt = select(TicketSequence).where(TicketSequence.tenant_id == tenant_id, TicketSequence.prefix == prefix)
    seq = session.scalars(stmt.with_for_update()).first()
    if seq is None:
        session.execute(pg_insert(TicketSequence).values(tenant_id=tenant_id, prefix=prefix, last_value=0)
                        .on_conflict_do_nothing(index_elements=["tenant_id", "prefix"]))
        seq = session.scalars(stmt.with_for_update()).one()
    seq.last_value += 1
    session.flush()
    return f"{prefix}-{seq.last_value:06d}"


def compute_priority(session: Session, tenant_id: int, impact: int, urgency: int):
    m = session.scalars(select(PriorityMatrix).where(PriorityMatrix.tenant_id == tenant_id,
                                                     PriorityMatrix.impact == impact,
                                                     PriorityMatrix.urgency == urgency)).first()
    if m:
        return m.priority
    return session.scalars(select(Priority).where(Priority.tenant_id == tenant_id)
                           .order_by(Priority.level.desc()).limit(1)).first()


def auto_assign(session: Session, ticket):
    team = ticket.team
    if ticket.assignee is not None or not team or team.assignment_strategy == "manual":
        return None
    # Only active, available staff are eligible: membership alone must not make a requester an assignee (U7-A2).
    members = [m for m in sorted(team.members, key=lambda u: u.id)
               if m.is_active and m.is_available and m.role in STAFF_ROLES]
    if not members:
        return None
    if team.assignment_strategy == "round_robin":
        pick = members[team.rr_cursor % len(members)]
        team.rr_cursor = (team.rr_cursor + 1) % len(members)
    else:  # least_loaded
        rows = session.execute(
            select(Ticket.assignee_id, func.count()).join(State, State.id == Ticket.state_id)
            .where(Ticket.assignee_id.in_([m.id for m in members]), State.category.notin_(DONE_CATEGORIES))
            .group_by(Ticket.assignee_id)).all()
        loads = {uid: n for uid, n in rows}
        pick = min(members, key=lambda m: (loads.get(m.id, 0), m.id))
    ticket.assignee = pick
    session.flush()
    log_event(session, ticket, "assigned", None, assignee=pick.display_name, strategy=team.assignment_strategy)
    notifications.notify_assignment(session, ticket)
    return pick


def _enqueue_ai(session: Session, ticket) -> None:
    tenant_id, ticket_id = ticket.tenant_id, ticket.id

    def _go() -> None:
        from ..worker.tasks import enrich_ticket
        enrich_ticket.delay(tenant_id, ticket_id)

    commit_hooks.on_commit(session, _go, robust=True)  # secondary to the committed change: log, don't fail it


def create_ticket(session: Session, *, tenant_id, ticket_type, subject, description="", requester=None, account=None,
                  source="portal", channel=None, created_by=None, impact=None, urgency=None, category=None,
                  service=None, ci=None, team=None, assignee=None, custom_fields=None, tags=None,
                  is_major_incident=False, priority=None) -> Ticket:
    assert_session_tenant(session, tenant_id)
    if account is None and requester is not None:
        account = requester.account
    impact = impact or ticket_type.default_impact
    urgency = urgency or ticket_type.default_urgency
    if team is None:
        for src in (category and category.default_team, service and service.owner_team, ci and ci.owner_team):
            if src:
                team = src
                break
    workflow = ticket_type.workflow
    initial = (next((s for s in workflow.states if s.is_initial), None)
               or next(iter(sorted(workflow.states, key=lambda s: (s.order, s.id))), None))
    prio = priority or compute_priority(session, tenant_id, impact, urgency)
    t = Ticket(
        tenant_id=tenant_id, number=next_number(session, tenant_id, ticket_type.prefix), ticket_type=ticket_type,
        state=initial, subject=subject[:300], description=description,
        requester=requester, account=account, source=source, channel=channel, created_by=created_by,
        impact=impact, urgency=urgency, category=category, service=service, ci=ci, team=team,
        assignee=assignee, custom_fields=custom_fields or {}, tags=list(tags or []),
        is_major_incident=is_major_incident, priority=prio,
        ticket_type_id=ticket_type.id, state_id=initial.id, priority_id=prio.id if prio else None,
    )
    t.created_at = utcnow()
    sla.apply_sla(session, t)
    session.add(t)
    session.flush()
    if requester is not None:
        session.add(TicketWatcher(ticket_id=t.id, user_id=requester.id))
    log_event(session, t, "created", created_by, source=source, priority=t.priority.key if t.priority else None)
    emit(session, t, "ticket.created", actor=created_by)
    session.flush()
    session.refresh(t)
    if t.assignee_id:
        # Assigned at creation: auto_assign() leaves such tickets alone, so notify here. Same transaction as the
        # ticket, so a rolled-back creation leaves no notification behind.
        notifications.notify_assignment(session, t, actor=created_by)
    else:
        auto_assign(session, t)
    _enqueue_ai(session, t)
    # robust: the ticket is already committed when this runs; a failing notification is logged instead of
    # surfacing as an error that invites the user to submit it again.
    commit_hooks.on_commit(session, _notify_after_create(t.tenant_id, t.id), robust=True)
    return t


def _in_new_tenant_session(tenant_id: int, ticket_id: int, fn):
    """After-commit work runs in a fresh tenant session on the persisted row (the original transaction is over)."""
    def _run() -> None:
        with tenant_session(tenant_id) as s:
            t = s.get(Ticket, ticket_id)
            if t is not None:
                fn(s, t)
    return _run


def _notify_after_create(tenant_id: int, ticket_id: int):
    return _in_new_tenant_session(tenant_id, ticket_id, lambda s, t: notifications.notify_ticket_created(s, t))


def update_ticket(session: Session, ticket, user, data: dict, request=None):
    allowed = AGENT_EDITABLE if (user is None or user.is_agent) else CUSTOMER_EDITABLE
    changes: dict = {}
    old_team = ticket.team
    for field, new in data.items():
        if field not in allowed:
            continue
        if field == "custom_fields":
            new = {**(ticket.custom_fields or {}), **(new or {})}
        old = getattr(ticket, field)
        if _ident(old) != _ident(new):
            changes[field] = [_jsonable(old), _jsonable(new)]
            setattr(ticket, field, new)
    if not changes:
        return ticket, {}
    if ("impact" in changes or "urgency" in changes) and "priority" not in changes:
        p = compute_priority(session, ticket.tenant_id, ticket.impact, ticket.urgency)
        if p and (ticket.priority is None or p.id != ticket.priority.id):
            changes["priority"] = [_jsonable(ticket.priority), _jsonable(p)]
            ticket.priority = p
    session.flush()
    if "priority" in changes or "account" in changes:
        sla.apply_sla(session, ticket, recompute=True)
    session.flush()
    log_event(session, ticket, "updated", user, changes=changes)
    audit(session, ticket.tenant_id, "ticket.update", ticket, user, changes, request)
    if "assignee" in changes and ticket.assignee_id:
        notifications.notify_assignment(session, ticket)
    if "team" in changes and not ticket.assignee_id:
        auto_assign(session, ticket)
    if "team" in changes and old_team is not None and ticket.team is not None and old_team.id != ticket.team.id:
        notifications.notify_team_transfer(session, ticket, old_team, ticket.team)
    emit(session, ticket, "ticket.updated", actor=user, changes=changes)
    if {"subject", "description", "category"} & changes.keys():
        _enqueue_ai(session, ticket)
    return ticket, changes


def set_state(session: Session, ticket, new_state, actor=None, via=None):
    old = ticket.state
    if old.id == new_state.id:
        return ticket
    ticket.state = new_state
    sla.on_state_change(ticket, old, new_state)
    session.flush()
    log_event(session, ticket, "state_changed", actor, **{"from": old.name, "to": new_state.name, "via": via or ""})
    emit(session, ticket, "ticket.state_changed", actor=actor, changes={"state": [old.key, new_state.key]})
    if new_state.category == "resolved" and old.category != "resolved":
        _on_resolved(session, ticket)
    return ticket


def _on_resolved(session: Session, ticket) -> None:
    csat_on = ticket.tenant.setting("csat_enabled", True)
    survey = None
    if csat_on and ticket.requester_id and ticket.csat is None:
        survey = CSATSurvey(tenant_id=ticket.tenant_id, ticket_id=ticket.id)
        session.add(survey)
        session.flush()
    survey_token = survey.token if survey else None
    # Runs only if the resolution commits, in the ticket's own tenant; a notification failure never fails the
    # committed transition.
    commit_hooks.on_commit(session, _in_new_tenant_session(
        ticket.tenant_id, ticket.id,
        lambda s, t: notifications.notify_resolved(s, t, s.scalars(select(CSATSurvey).where(
            CSATSurvey.token == survey_token)).first() if survey_token else None)), robust=True)


def perform_transition(session: Session, ticket, transition, user, comment="", fields=None, comment_public=None):
    if fields:
        for k, v in fields.items():
            if k in ("resolution_code", "resolution_notes"):
                setattr(ticket, k, v)
            else:
                ticket.custom_fields = {**(ticket.custom_fields or {}), k: v}
    validate_transition(ticket, transition, user, comment)
    if fields:
        session.flush()
    if comment and comment.strip():
        public = comment_public if comment_public is not None else (user is not None and not user.is_agent)
        add_interaction(session, ticket, body=comment, author=user, kind="public" if public else "internal",
                        source="portal", reopen=False)
    return set_state(session, ticket, transition.to_state, actor=user, via=transition.name)


def add_interaction(session: Session, ticket, *, body, author=None, kind="public", source="portal", channel=None,
                    direction=None, external_message_id="", reopen=True, deliver=True) -> Interaction:
    if direction is None:
        if author is not None and author.is_agent:
            direction = "outbound" if kind == "public" else "internal"
        elif kind == "system":
            direction = "internal"
        else:
            direction = "inbound"
    it = Interaction(
        tenant_id=ticket.tenant_id, ticket_id=ticket.id, author_id=getattr(author, "id", None), kind=kind,
        direction=direction, body=body, source=source, channel_id=getattr(channel, "id", None),
        external_message_id=external_message_id,
        delivery_status="pending" if (direction == "outbound" and deliver) else "n/a",
    )
    it.created_at = utcnow()
    session.add(it)
    session.flush()
    if direction == "outbound" and kind == "public" and sla.record_first_response(ticket, it.created_at):
        session.flush()
        log_event(session, ticket, "first_response", author)
    log_event(session, ticket, "interaction", author, kind=kind, direction=direction, interaction_id=it.id)
    if direction == "inbound" and kind == "public":
        wf = ticket.state.workflow
        if reopen and wf.customer_reply_state_id and ticket.state.category in ("pending", "resolved"):
            reply_state = session.get(State, wf.customer_reply_state_id)
            set_state(session, ticket, reply_state, actor=author, via="customer reply")
        emit(session, ticket, "ticket.customer_replied", actor=author)
        ticket.updated_at = utcnow()
        session.flush()
    if direction == "outbound" and deliver:
        tenant_id, interaction_id = ticket.tenant_id, it.id

        def _deliver() -> None:
            from ..worker.tasks import deliver_interaction
            deliver_interaction.delay(tenant_id, interaction_id)

        commit_hooks.on_commit(session, _deliver)
    return it


def merge_into(session: Session, duplicate, primary, user):
    """Close `duplicate` as a duplicate of `primary`, copying the conversation reference."""
    exists = session.scalars(select(TicketLink).where(
        TicketLink.source_id == duplicate.id, TicketLink.target_id == primary.id,
        TicketLink.link_type == "duplicate_of")).first()
    if exists is None:
        session.add(TicketLink(tenant_id=duplicate.tenant_id, source_id=duplicate.id, target_id=primary.id,
                               link_type="duplicate_of", created_by_id=getattr(user, "id", None)))
    add_interaction(session, primary, body=f"{duplicate.number} merged into this ticket: {duplicate.subject}",
                    author=user, kind="internal", deliver=False)
    add_interaction(session, duplicate, body=f"Closed as duplicate of {primary.number}.", author=user,
                    kind="internal", deliver=False)
    final = session.scalars(select(State).where(State.workflow_id == duplicate.state.workflow_id,
                                                State.category.in_(["cancelled", "closed"]))
                            .order_by(State.category).limit(1)).first()
    if final:
        set_state(session, duplicate, final, actor=user, via="merge")
    have = set(session.scalars(select(TicketWatcher.user_id).where(TicketWatcher.ticket_id == primary.id)))
    for w in duplicate.watchers:
        if w.id not in have:
            session.add(TicketWatcher(ticket_id=primary.id, user_id=w.id))
    session.flush()
    return primary


def assert_transition(ticket, transition, user, comment=""):
    try:
        validate_transition(ticket, transition, user, comment)
        return None
    except TransitionError as e:
        return str(e)


def resolve_approver_for_request(session: Session, employee, approval_type="manager"):
    """Determine the authorized approver following organizational hierarchy and fallbacks.
    Returns (approver_user, routing_reason)."""
    tenant_id = employee.tenant_id

    if approval_type == "manager":
        mgr = employee.manager
        if mgr and mgr.is_active and mgr.id != employee.id and mgr.tenant_id == tenant_id:
            return mgr, "direct_manager"
        if employee.department_id and employee.department.head:  # fallback 1: department head
            head = employee.department.head
            if head.is_active and head.id != employee.id and head.tenant_id == tenant_id:
                return head, "department_head_fallback"
    elif approval_type == "dept_head":
        if employee.department_id and employee.department.head:
            head = employee.department.head
            if head.is_active and head.id != employee.id and head.tenant_id == tenant_id:
                return head, "department_head"

    # Fallback 2: active tenant manager or team lead (excluding the requester themselves)
    admin_mgr = session.scalars(
        select(User).where(User.tenant_id == tenant_id, User.role.in_(PRIVILEGED_ROLES), User.is_active.is_(True),
                           User.id != employee.id).order_by(User.role, User.id).limit(1)).first()
    if admin_mgr:
        return admin_mgr, "tenant_admin_fallback"
    return None, "none"


def create_service_request_from_catalog(session: Session, catalog_item, employee, fields, additional_notes=""):
    """Create a service request from a catalog item, validating form fields and routing approvals."""
    if fields is None:
        fields = {}
    elif not isinstance(fields, dict):
        raise ValidationFailed({"fields": "Form fields must be provided as a key-value object."})

    errors: dict = {}
    raw_schema = catalog_item.form_schema
    if isinstance(raw_schema, dict):
        field_list = raw_schema.get("fields", [])
    elif isinstance(raw_schema, list):
        field_list = raw_schema
    else:
        field_list = []

    validated_custom: dict = {}
    for field_def in field_list:
        if not isinstance(field_def, dict):
            continue
        key = field_def.get("name") or field_def.get("key")
        if not key:
            continue
        label = field_def.get("label", key)
        required = field_def.get("required", False)
        ftype = field_def.get("type", "text")
        val = fields.get(key)

        if required and (val is None or val == "" or val == []):
            errors[key] = f"{label} is required."
        elif val is not None and val != "":
            if ftype == "number":
                try:
                    float(val)
                except (ValueError, TypeError):
                    errors[key] = f"{label} must be a valid number."
            elif ftype == "select":
                options = field_def.get("options", [])
                if options and val not in options:
                    errors[key] = f"{val} is not a valid option for {label}."
            validated_custom[key] = val

    if errors:
        raise ValidationFailed({"detail": "; ".join(errors.values()), **errors})

    desc_lines: list[str] = []
    if additional_notes:
        desc_lines.append(additional_notes.strip())
        desc_lines.append("")
    if validated_custom:
        desc_lines.append("Requested Service Details:")
        for k, v in validated_custom.items():
            desc_lines.append(f"- {k}: {v}")
    full_description = "\n".join(desc_lines) if desc_lines else (catalog_item.description or "")

    ticket = create_ticket(
        session, tenant_id=catalog_item.tenant_id, ticket_type=catalog_item.ticket_type,
        subject=f"{catalog_item.name} - {employee.display_name}", description=full_description,
        requester=employee, team=catalog_item.target_team, category=catalog_item.category,
        custom_fields=validated_custom, source="portal")

    approver = None
    if catalog_item.requires_approval:
        approver, routing_reason = resolve_approver_for_request(session, employee, catalog_item.approval_type)
        if not approver:
            raise ValidationFailed({"approval": "No eligible active approver found in organization hierarchy."})
        if approver.id == employee.id:
            raise ValidationFailed({"approval": "Self-approval is prohibited."})

        session.add(Approval(tenant_id=ticket.tenant_id, ticket_id=ticket.id, approver_id=approver.id,
                             requested_by_id=employee.id, status="pending"))
        session.flush()
        log_event(session, ticket, "approval_requested", employee, approver=approver.display_name,
                  routing_reason=routing_reason)

        approval_state = session.scalars(select(State).where(State.workflow_id == ticket.state.workflow_id,
                                                             State.key == "awaiting_approval")).first()
        if approval_state:
            set_state(session, ticket, approval_state, actor=employee, via="Send for approval")

        if approver.email:
            notifications.send(
                session, [approver.email], f"[{ticket.number}] Approval requested: {ticket.subject}",
                f"{employee.display_name} requested {catalog_item.name}.\n"
                f"Please review and approve: {notifications.ticket_url(ticket)}")
    return ticket, approver
