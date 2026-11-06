"""Installs a sensible ITIL-aligned starting configuration for a tenant.
Everything created here is editable afterwards in the configuration console."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (BusinessHours, CannedResponse, Category, CustomField, Priority, PriorityMatrix, SLAPolicy,
                         SLAPolicyTicketType, SLATarget, State, Tenant, TicketType, Transition, Workflow)

WEEKDAYS_9_18 = {str(d): [["09:00", "18:00"]] for d in range(5)}

# key, name, category, initial, pauses_sla, color
WORKFLOWS = {
    "Incident": {
        "states": [
            ("new", "New", "new", True, False, "#6B7A90"),
            ("in_progress", "In progress", "open", False, False, "#2F6FDE"),
            ("pending_customer", "Waiting on customer", "pending", False, True, "#B7791F"),
            ("pending_vendor", "Waiting on third party", "pending", False, False, "#9A6B1F"),
            ("resolved", "Resolved", "resolved", False, False, "#2E7D4F"),
            ("closed", "Closed", "closed", False, False, "#475569"),
            ("cancelled", "Cancelled", "cancelled", False, False, "#94A3B8"),
        ],
        "reply_state": "in_progress",
        # name, from (None=any), to, roles, customer, comment, required_fields, approval
        "transitions": [
            ("Start work", "new", "in_progress", [], False, False, [], False),
            ("Ask customer", "in_progress", "pending_customer", [], False, True, [], False),
            ("Wait for third party", "in_progress", "pending_vendor", [], False, True, [], False),
            ("Resume", "pending_customer", "in_progress", [], False, False, [], False),
            ("Resume", "pending_vendor", "in_progress", [], False, False, [], False),
            ("Resolve", None, "resolved", [], False, False, ["resolution_code", "resolution_notes"], False),
            ("Reopen", "resolved", "in_progress", [], True, True, [], False),
            ("Confirm and close", "resolved", "closed", [], True, False, [], False),
            ("Cancel", None, "cancelled", ["employee", "team_lead", "manager"], False, True, [], False),
        ],
    },
    "Service request": {
        "states": [
            ("new", "New", "new", True, False, "#6B7A90"),
            ("awaiting_approval", "Awaiting approval", "pending", False, True, "#7C5CC4"),
            ("in_fulfilment", "In fulfilment", "open", False, False, "#2F6FDE"),
            ("pending_customer", "Waiting on customer", "pending", False, True, "#B7791F"),
            ("fulfilled", "Fulfilled", "resolved", False, False, "#2E7D4F"),
            ("closed", "Closed", "closed", False, False, "#475569"),
            ("rejected", "Rejected", "cancelled", False, False, "#94A3B8"),
        ],
        "reply_state": "in_fulfilment",
        "transitions": [
            ("Send for approval", "new", "awaiting_approval", [], False, False, [], False),
            ("Start fulfilment", "new", "in_fulfilment", [], False, False, [], False),
            ("Approved - fulfil", "awaiting_approval", "in_fulfilment", [], False, False, [], True),
            ("Reject", "awaiting_approval", "rejected", ["team_lead", "manager"], False, True, [], False),
            ("Ask customer", "in_fulfilment", "pending_customer", [], False, True, [], False),
            ("Resume", "pending_customer", "in_fulfilment", [], False, False, [], False),
            ("Mark fulfilled", "in_fulfilment", "fulfilled", [], False, False, ["resolution_notes"], False),
            ("Reopen", "fulfilled", "in_fulfilment", [], True, True, [], False),
            ("Close", "fulfilled", "closed", [], True, False, [], False),
        ],
    },
    "Problem": {
        "states": [
            ("new", "New", "new", True, False, "#6B7A90"),
            ("investigating", "Root cause analysis", "open", False, False, "#2F6FDE"),
            ("known_error", "Known error", "open", False, False, "#C2410C"),
            ("resolved", "Resolved", "resolved", False, False, "#2E7D4F"),
            ("closed", "Closed", "closed", False, False, "#475569"),
        ],
        "reply_state": None,
        "transitions": [
            ("Investigate", "new", "investigating", [], False, False, [], False),
            ("Record known error", "investigating", "known_error", [], False, True, ["root_cause"], False),
            ("Resolve", "investigating", "resolved", [], False, False, ["root_cause", "resolution_notes"], False),
            ("Resolve", "known_error", "resolved", [], False, False, ["resolution_notes"], False),
            ("Close", "resolved", "closed", ["team_lead", "manager"], False, False, [], False),
        ],
    },
    "Change": {
        "states": [
            ("draft", "Draft", "new", True, False, "#6B7A90"),
            ("assessment", "Assessment", "open", False, False, "#2F6FDE"),
            ("cab_review", "CAB review", "pending", False, False, "#7C5CC4"),
            ("scheduled", "Scheduled", "open", False, False, "#0E7490"),
            ("implementing", "Implementing", "open", False, False, "#C2410C"),
            ("review", "Post-implementation review", "resolved", False, False, "#2E7D4F"),
            ("closed", "Closed", "closed", False, False, "#475569"),
            ("rejected", "Rejected", "cancelled", False, False, "#94A3B8"),
        ],
        "reply_state": None,
        "transitions": [
            ("Submit for assessment", "draft", "assessment", [], False, False, ["change_type", "risk", "backout_plan"], False),
            ("Send to CAB", "assessment", "cab_review", [], False, False, ["implementation_window"], False),
            ("Approve and schedule", "cab_review", "scheduled", [], False, False, [], True),
            ("Reject", "cab_review", "rejected", ["team_lead", "manager"], False, True, [], False),
            ("Start implementation", "scheduled", "implementing", [], False, False, [], False),
            ("Complete", "implementing", "review", [], False, True, ["outcome"], False),
            ("Close", "review", "closed", ["team_lead", "manager"], False, False, [], False),
        ],
    },
}

TYPES = [  # key, name, prefix, workflow, customer_can_create, impact, urgency, order
    ("incident", "Incident", "INC", "Incident", True, 3, 3, 1),
    ("service_request", "Service request", "REQ", "Service request", True, 4, 4, 2),
    ("problem", "Problem", "PRB", "Problem", False, 2, 3, 3),
    ("change", "Change", "CHG", "Change", False, 3, 3, 4),
]

CUSTOM_FIELDS = {
    "incident": [("environment", "Environment", "select", ["Production", "Staging", "Development"], False, True),
                 ("error_message", "Error message or code", "textarea", [], False, True)],
    "service_request": [("requested_for", "Requested for (name or email)", "text", [], False, True),
                        ("needed_by", "Needed by", "date", [], False, True)],
    "problem": [("root_cause", "Root cause", "textarea", [], False, False),
                ("workaround", "Workaround", "textarea", [], False, False)],
    "change": [("change_type", "Change type", "select", ["Standard", "Normal", "Emergency"], False, False),
               ("risk", "Risk", "select", ["Low", "Medium", "High"], False, False),
               ("implementation_window", "Implementation window", "text", [], False, False),
               ("backout_plan", "Back-out plan", "textarea", [], False, False),
               ("outcome", "Outcome", "select", ["Successful", "Successful with issues", "Failed - rolled back"], False, False)],
}

PRIORITIES = [("P1", "Critical", 1, "#C2362B"), ("P2", "High", 2, "#D9772B"),
              ("P3", "Medium", 3, "#B8961C"), ("P4", "Low", 4, "#5B7A99")]

# minutes: (first response, resolution)
SLA_STANDARD = {"P1": (15, 240), "P2": (30, 540), "P3": (240, 1620), "P4": (480, 2700)}
SLA_PREMIUM = {"P1": (10, 120), "P2": (30, 360), "P3": (120, 1440), "P4": (240, 2880)}

CATEGORIES = [
    ("Access and identity", ["Login / SSO", "Permissions"]),
    ("Application error", ["Bug / defect", "Integration failure"]),
    ("Performance", ["Slowness", "Outage"]),
    ("Cloud and infrastructure", ["Compute", "Network", "Storage"]),
    ("Data and pipelines", ["Job failure", "Data quality"]),
    ("AI / ML", ["Model drift / accuracy", "Hallucination / unsafe output", "Inference latency / outage"]),
    ("Security", ["Suspicious activity", "Vulnerability"]),
]

CANNED = [
    ("Acknowledge and investigating",
     "Hi {{requester.first_name}},\n\nThanks for raising {{ticket.number}}. I'm looking into it now and will update you within the hour.\n\nRegards,\n{{agent.first_name}}"),
    ("Need more information",
     "Hi {{requester.first_name}},\n\nTo move {{ticket.number}} forward, could you share:\n- the exact error message or a screenshot\n- when it started\n- how many users are affected\n\nThanks,\n{{agent.first_name}}"),
    ("Resolved - please confirm",
     "Hi {{requester.first_name}},\n\nWe've applied a fix for {{ticket.number}}. Please confirm everything works on your side; if we don't hear back the ticket will close automatically in 5 days.\n\nRegards,\n{{agent.first_name}}"),
]




def _get_or_create(s: Session, model, lookup: dict, defaults: dict | None = None):
    obj = s.scalars(select(model).filter_by(**lookup)).first()
    if obj is not None:
        return obj, False
    obj = model(**lookup, **(defaults or {}))
    s.add(obj)
    s.flush()
    return obj, True


def install_defaults(s: Session, tenant: Tenant) -> dict:
    """Idempotent: re-running adds only what is missing. ``s`` must be bound to ``tenant``'s context."""
    tid = tenant.id
    tenant.settings = {"auto_close_days": 5, "customer_sees_account_tickets": True, "csat_enabled": True,
                       "auto_apply_triage": False, "triage_confidence_threshold": 0.8, **(tenant.settings or {})}
    s.flush()
    std_hours, _ = _get_or_create(s, BusinessHours, {"tenant_id": tid, "name": "Standard (Mon-Fri 09:00-18:00)"},
                                  {"timezone": tenant.timezone, "schedule": WEEKDAYS_9_18, "is_24x7": False, "holidays": []})
    _get_or_create(s, BusinessHours, {"tenant_id": tid, "name": "24x7"},
                   {"is_24x7": True, "timezone": tenant.timezone, "schedule": {}, "holidays": []})

    prios = {}
    for key, name, level, color in PRIORITIES:
        prios[key], _ = _get_or_create(s, Priority, {"tenant_id": tid, "key": key}, {"name": name, "level": level, "color": color})
    for impact in range(1, 5):
        for urgency in range(1, 5):
            total = impact + urgency
            key = "P1" if total <= 3 else "P2" if total == 4 else "P3" if total <= 6 else "P4"
            _get_or_create(s, PriorityMatrix, {"tenant_id": tid, "impact": impact, "urgency": urgency},
                           {"priority_id": prios[key].id})

    workflows = {}
    for wname, spec in WORKFLOWS.items():
        wf, created = _get_or_create(s, Workflow, {"tenant_id": tid, "name": wname}, {"description": ""})
        workflows[wname] = wf
        if not created:
            continue
        states = {}
        for i, (key, name, cat, initial, pauses, color) in enumerate(spec["states"]):
            st = State(workflow_id=wf.id, key=key, name=name, category=cat, is_initial=initial, pauses_sla=pauses,
                       order=i, color=color)
            s.add(st)
            s.flush()
            states[key] = st
        for i, (name, frm, to, roles, cust, comment, req, appr) in enumerate(spec["transitions"]):
            s.add(Transition(workflow_id=wf.id, name=name, from_state_id=states[frm].id if frm else None,
                             to_state_id=states[to].id, allowed_roles=roles, customer_allowed=cust,
                             requires_comment=comment, required_fields=req, requires_approval=appr, order=i))
        if spec["reply_state"]:
            wf.customer_reply_state_id = states[spec["reply_state"]].id
        s.flush()
        s.refresh(wf)

    types = {}
    for key, name, prefix, wname, cust, imp, urg, order in TYPES:
        types[key], _ = _get_or_create(s, TicketType, {"tenant_id": tid, "key": key}, {
            "name": name, "prefix": prefix, "workflow_id": workflows[wname].id, "customer_can_create": cust,
            "default_impact": imp, "default_urgency": urg, "order": order, "is_active": True})
        for i, (fkey, label, ftype, opts, req, vis) in enumerate(CUSTOM_FIELDS.get(key, [])):
            _get_or_create(s, CustomField, {"tenant_id": tid, "ticket_type_id": types[key].id, "key": fkey}, {
                "label": label, "field_type": ftype, "options": opts, "required": req, "visible_to_customer": vis,
                "order": i, "help_text": ""})

    for pname, table, hours, default in (("Standard SLA (business hours)", SLA_STANDARD, std_hours, True),
                                         ("Premium SLA (24x7)", SLA_PREMIUM, None, False)):
        pol, created = _get_or_create(s, SLAPolicy, {"tenant_id": tid, "name": pname},
                                      {"is_default": default, "business_hours_id": hours.id if hours else None,
                                       "at_risk_percent": 75})
        if created:
            for tkey in ("incident", "service_request"):
                s.add(SLAPolicyTicketType(slapolicy_id=pol.id, tickettype_id=types[tkey].id))
            for key, (resp, res) in table.items():
                s.add(SLATarget(policy_id=pol.id, priority_id=prios[key].id, first_response_minutes=resp,
                                resolution_minutes=res))

    for parent, children in CATEGORIES:
        p = s.scalars(select(Category).where(Category.tenant_id == tid, Category.name == parent,
                                             Category.parent_id.is_(None))).first()
        if p is None:
            p = Category(tenant_id=tid, name=parent)
            s.add(p)
            s.flush()
        for c in children:
            if not s.scalars(select(Category.id).where(Category.tenant_id == tid, Category.name == c,
                                                       Category.parent_id == p.id)).first():
                s.add(Category(tenant_id=tid, name=c, parent_id=p.id))
    req = types["service_request"]
    for c in ("New access", "Software or environment", "Data extract"):
        if not s.scalars(select(Category.id).where(Category.tenant_id == tid, Category.name == c,
                                                   Category.parent_id.is_(None))).first():
            s.add(Category(tenant_id=tid, name=c, ticket_type_id=req.id))

    for title, body in CANNED:
        _get_or_create(s, CannedResponse, {"tenant_id": tid, "title": title}, {"body": body})
    s.flush()
    return {"workflows": workflows, "types": types, "priorities": prios}
