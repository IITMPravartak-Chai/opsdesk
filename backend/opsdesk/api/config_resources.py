"""Resource definitions for the configuration console.

Each resource lists its writable fields, search columns, delete guards and side effects. The generic handlers in
``routers/admin_config.py`` do the rest. Rules that the old admin enforced (tenant scoping, priorities in use cannot be
deleted, channel kinds must be registered, SLA/business-hours validation) are kept; guards the old admin lacked and
that prevent silent cascades are added and explained next to the resource.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Callable, Optional

from sqlalchemy import delete, func, select

from ..channels.registry import adapter_classes
from ..db.models import (Account, BusinessHours, CannedResponse, Category, Channel, CIDependency, ConfigurationItem,
                         ContactIdentity, CustomField, InboundMessage, KnowledgeArticle, Priority, PriorityMatrix, Rule,
                         Service, ServiceCatalogItem, SLAPolicy, SLATarget, State, Team, Ticket, TicketType, Transition,
                         User, Workflow)
from ..db.models.cmdb import CI_STATUSES, CI_TYPES, ENVIRONMENTS
from ..db.models.core import PRIVILEGED_ROLES, ROLES, STAFF_ROLES, TIERS
from ..db.models.misc import RULE_EVENTS
from ..db.models.workflow import FIELD_TYPES, STATE_CATEGORIES
from . import admin_rules as rules
from .config_fields import F, FieldError

MASK = "********"
SECRET_KEY_RE = re.compile(r"(secret|token|password|passwd|pwd|key|credential)", re.I)

CONDITION_OPS = ("eq", "ne", "in", "not_in", "contains", "not_contains", "gt", "lt", "is_empty", "not_empty", "changed")
ACTION_TYPES = ("assign_team", "assign_user", "set_priority", "add_tag", "set_major_incident", "set_field",
                "transition_to", "add_note", "notify", "webhook")


class Resource:
    slug = ""
    label = ""
    model: Any = None
    fields: dict = {}
    search: tuple = ()
    order: tuple = ()
    parent: Optional[tuple] = None          # (parent_slug, fk_column_name) for child resources
    roles = PRIVILEGED_ROLES
    audit_name = ""
    can_create = True
    can_delete = True
    list_filters: dict = {}                  # query parameter -> column name

    # --- scoping -------------------------------------------------------------------------------
    def scope(self, tid: int, parent_id: Optional[int] = None) -> list:
        conds = [self.model.tenant_id == tid] if hasattr(self.model, "tenant_id") else []
        if self.parent and parent_id is not None:
            conds.append(getattr(self.model, self.parent[1]) == parent_id)
        return conds

    # --- hooks ---------------------------------------------------------------------------------
    def validate(self, ctx, cleaned: dict, obj) -> None: ...
    def before_create(self, ctx, cleaned: dict, parent_id) -> dict: return cleaned
    def apply(self, ctx, obj, cleaned: dict) -> None:
        for k, v in cleaned.items():
            setattr(obj, self.fields[k].attr or k, v)
    def after_save(self, ctx, obj, cleaned: dict, created: bool) -> None: ...
    def guard_delete(self, ctx, obj) -> None: ...
    def extra(self, ctx, obj, detail: bool) -> dict: return {}
    def serialize_value(self, ctx, obj, name: str, spec: F):
        return getattr(obj, spec.attr or name)

    def diff_state(self, ctx, obj) -> dict:
        return {k: self.serialize_value(ctx, obj, k, f) for k, f in self.fields.items() if not f.read_only and not f.secret}

    def describe(self) -> dict:
        return {"slug": self.slug, "label": self.label, "can_create": self.can_create, "can_delete": self.can_delete,
                "search": bool(self.search), "parent": self.parent[0] if self.parent else None,
                "fields": {k: f.describe() for k, f in self.fields.items()}}


def _in_use(**counts) -> None:
    used = {k: v for k, v in counts.items() if v}
    if used:
        raise rules.RuleError(409, "This is still in use, so it can't be deleted.", {"usage": used})


def _count(ctx, model, *conds) -> int:
    return ctx.s.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _unique(ctx, model, col, value, *, exclude=None, extra=()) -> None:
    stmt = select(model.id).where(col == value, *extra)
    if exclude is not None:
        stmt = stmt.where(model.id != exclude)
    if ctx.s.scalars(stmt.limit(1)).first():
        raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {col.key: "This value is already in use."}})


# ===================================================================================== workflows
class Workflows(Resource):
    slug, label, model = "workflows", "Workflows", Workflow
    search, order = ("name",), (Workflow.name,)
    fields = {"name": F("str", required=True, max_len=120), "description": F("text", default=""),
              "customer_reply_state_id": F("fk", nullable=True, model=State, label="name",
                                           help="Where a pending/resolved ticket goes when the customer replies.")}

    def validate(self, ctx, cleaned, obj):
        sid = cleaned.get("customer_reply_state_id")
        if sid:
            if obj is None or ctx.s.scalar(select(State.workflow_id).where(State.id == sid)) != obj.id:
                raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {
                    "customer_reply_state_id": "Choose a status that belongs to this workflow."}})

    def before_create(self, ctx, cleaned, parent_id):
        cleaned.pop("customer_reply_state_id", None)  # a new workflow has no statuses yet
        return cleaned

    def extra(self, ctx, obj, detail):
        out = {"state_count": _count(ctx, State, State.workflow_id == obj.id),
               "transition_count": _count(ctx, Transition, Transition.workflow_id == obj.id)}
        if detail:
            out["states"] = [States().row(ctx, st) for st in ctx.s.scalars(
                select(State).where(State.workflow_id == obj.id).order_by(State.order, State.id))]
            out["transitions"] = [Transitions().row(ctx, t) for t in ctx.s.scalars(
                select(Transition).where(Transition.workflow_id == obj.id).order_by(Transition.order, Transition.id))]
        return out

    def guard_delete(self, ctx, obj):
        _in_use(ticket_types=_count(ctx, TicketType, TicketType.workflow_id == obj.id))


class _Child(Resource):
    def row(self, ctx, obj) -> dict:
        d = {"id": obj.id}
        d.update({k: self.serialize_value(ctx, obj, k, f) for k, f in self.fields.items() if not f.secret})
        return d


class States(_Child):
    slug, label, model, parent = "states", "Statuses", State, ("workflows", "workflow_id")
    order = (State.order, State.id)
    fields = {"key": F("slug", required=True, max_len=40), "name": F("str", required=True, max_len=80),
              "category": F("choice", choices=STATE_CATEGORIES, default="open"),
              "is_initial": F("bool", default=False), "pauses_sla": F("bool", default=False),
              "order": F("int", min=0, max=100000, default=0), "color": F("color", default="#64748b")}

    def scope(self, tid, parent_id=None):
        conds = [State.workflow_id.in_(select(Workflow.id).where(Workflow.tenant_id == tid))]
        if parent_id is not None:
            conds.append(State.workflow_id == parent_id)
        return conds

    def validate(self, ctx, cleaned, obj):
        if "key" in cleaned:
            wf = obj.workflow_id if obj is not None else ctx.parent_id
            _unique(ctx, State, State.key, cleaned["key"], exclude=obj.id if obj else None, extra=(State.workflow_id == wf,))

    def after_save(self, ctx, obj, cleaned, created):
        if cleaned.get("is_initial"):  # exactly one initial status per workflow
            for other in ctx.s.scalars(select(State).where(State.workflow_id == obj.workflow_id, State.id != obj.id,
                                                           State.is_initial.is_(True))):
                other.is_initial = False

    def guard_delete(self, ctx, obj):
        _in_use(tickets=_count(ctx, Ticket, Ticket.state_id == obj.id))


class Transitions(_Child):
    slug, label, model, parent = "transitions", "Transitions", Transition, ("workflows", "workflow_id")
    order = (Transition.order, Transition.id)
    fields = {"name": F("str", required=True, max_len=80),
              "from_state_id": F("fk", nullable=True, model=State, default=None,
                                 help="Empty = from any non-final status."),
              "to_state_id": F("fk", required=True, model=State),
              "allowed_roles": F("str_list", choices=ROLES, default=list, help="Empty = any staff role."),
              "customer_allowed": F("bool", default=False), "requires_comment": F("bool", default=False),
              "required_fields": F("str_list", max_len=60, default=list),
              "requires_approval": F("bool", default=False),
              "order": F("int", min=0, max=100000, default=0)}

    def scope(self, tid, parent_id=None):
        conds = [Transition.workflow_id.in_(select(Workflow.id).where(Workflow.tenant_id == tid))]
        if parent_id is not None:
            conds.append(Transition.workflow_id == parent_id)
        return conds

    def validate(self, ctx, cleaned, obj):
        wf = obj.workflow_id if obj is not None else ctx.parent_id
        errors = {}
        for f in ("from_state_id", "to_state_id"):
            sid = cleaned.get(f)
            if sid and ctx.s.scalar(select(State.workflow_id).where(State.id == sid)) != wf:
                errors[f] = "Choose a status that belongs to this workflow."
        if errors:
            raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": errors})


# ===================================================================================== ticket types
class TicketTypes(Resource):
    slug, label, model = "ticket-types", "Ticket types", TicketType
    search, order = ("name", "key"), (TicketType.order, TicketType.name)
    fields = {"key": F("slug", required=True, max_len=40, lower=True), "name": F("str", required=True, max_len=80),
              "prefix": F("prefix", required=True, help="Ticket number prefix, e.g. INC."),
              "workflow_id": F("fk", required=True, model=Workflow),
              "default_impact": F("int", min=1, max=4, default=3), "default_urgency": F("int", min=1, max=4, default=3),
              "customer_can_create": F("bool", default=True), "is_active": F("bool", default=True),
              "order": F("int", min=0, max=100000, default=0)}

    def validate(self, ctx, cleaned, obj):
        if "key" in cleaned:
            _unique(ctx, TicketType, TicketType.key, cleaned["key"], exclude=obj.id if obj else None,
                    extra=(TicketType.tenant_id == ctx.tid,))

    def extra(self, ctx, obj, detail):
        out = {"custom_field_count": _count(ctx, CustomField, CustomField.ticket_type_id == obj.id)}
        if detail:
            out["custom_fields"] = [CustomFields().row(ctx, c) for c in ctx.s.scalars(
                select(CustomField).where(CustomField.ticket_type_id == obj.id).order_by(CustomField.order, CustomField.id))]
        return out

    def guard_delete(self, ctx, obj):
        _in_use(tickets=_count(ctx, Ticket, Ticket.ticket_type_id == obj.id),
                catalog_items=_count(ctx, ServiceCatalogItem, ServiceCatalogItem.ticket_type_id == obj.id),
                categories=_count(ctx, Category, Category.ticket_type_id == obj.id),
                canned_responses=_count(ctx, CannedResponse, CannedResponse.ticket_type_id == obj.id))


def _options_check(v):
    if any(len(x) > 120 for x in v):
        raise FieldError("Each option may have at most 120 characters.")


class CustomFields(_Child):
    slug, label, model, parent = "custom-fields", "Custom fields", CustomField, ("ticket-types", "ticket_type_id")
    order = (CustomField.order, CustomField.id)
    fields = {"key": F("slug", required=True, max_len=40), "label": F("str", required=True, max_len=120),
              "field_type": F("choice", choices=FIELD_TYPES, default="text"),
              "options": F("str_list", check=_options_check, default=list),
              "required": F("bool", default=False), "visible_to_customer": F("bool", default=True),
              "help_text": F("str", max_len=200, default=""), "order": F("int", min=0, max=100000, default=0)}

    def validate(self, ctx, cleaned, obj):
        tt = obj.ticket_type_id if obj is not None else ctx.parent_id
        if "key" in cleaned:
            _unique(ctx, CustomField, CustomField.key, cleaned["key"], exclude=obj.id if obj else None,
                    extra=(CustomField.ticket_type_id == tt,))
        ftype = cleaned.get("field_type", obj.field_type if obj is not None else "text")
        options = cleaned.get("options", obj.options if obj is not None else [])
        if ftype in ("select", "multiselect") and not options:
            raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {"options": "Add at least one option."}})


# ===================================================================================== priorities
class Priorities(Resource):
    slug, label, model = "priorities", "Priorities", Priority
    order = (Priority.level,)
    fields = {"key": F("str", required=True, max_len=10), "name": F("str", required=True, max_len=60),
              "level": F("int", required=True, min=1, max=99, help="1 = most urgent."),
              "color": F("color", default="#64748b")}

    def validate(self, ctx, cleaned, obj):
        if "key" in cleaned:
            _unique(ctx, Priority, Priority.key, cleaned["key"], exclude=obj.id if obj else None,
                    extra=(Priority.tenant_id == ctx.tid,))

    def guard_delete(self, ctx, obj):
        # The old admin refused while SLA targets used it (deleting would cascade them); matrix cells cascade the same
        # way and would leave the impact x urgency grid with holes, so they block it too.
        _in_use(sla_targets=_count(ctx, SLATarget, SLATarget.priority_id == obj.id),
                priority_matrix_cells=_count(ctx, PriorityMatrix, PriorityMatrix.priority_id == obj.id))


# ===================================================================================== categories
class Categories(Resource):
    slug, label, model = "categories", "Categories", Category
    search, order = ("name",), (Category.name,)
    fields = {"name": F("str", required=True, max_len=120),
              "parent_id": F("fk", nullable=True, model=Category, default=None),
              "ticket_type_id": F("fk", nullable=True, model=TicketType, default=None, help="Empty = all types."),
              "default_team_id": F("fk", nullable=True, model=Team, default=None)}

    def validate(self, ctx, cleaned, obj):
        pid = cleaned.get("parent_id")
        if pid and obj is not None:
            seen, cur = {obj.id}, pid
            while cur:  # no category may become its own ancestor
                if cur in seen:
                    raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {
                        "parent_id": "A category cannot be placed under itself or its own sub-category."}})
                seen.add(cur)
                cur = ctx.s.scalar(select(Category.parent_id).where(Category.id == cur))

    def serialize_value(self, ctx, obj, name, spec):
        return getattr(obj, name)

    def extra(self, ctx, obj, detail):
        return {"path": str(obj)}

    def guard_delete(self, ctx, obj):
        _in_use(sub_categories=_count(ctx, Category, Category.parent_id == obj.id))


# ===================================================================================== accounts
class Accounts(Resource):
    slug, label, model = "accounts", "Accounts", Account
    search, order = ("name", "code"), (Account.name,)
    list_filters = {"tier": "tier", "is_active": "is_active"}
    fields = {"name": F("str", required=True, max_len=200), "code": F("str", required=True, max_len=20, upper=True),
              "tier": F("choice", choices=TIERS, default="standard"),
              "email_domains": F("domain_list", default=list, help="Inbound senders from these domains map to this account."),
              "is_active": F("bool", default=True), "notes": F("text", default=""),
              "sla_policy_id": F("fk", nullable=True, model=SLAPolicy, default=None, admin_only=True),
              "business_hours_id": F("fk", nullable=True, model=BusinessHours, default=None, admin_only=True)}

    def validate(self, ctx, cleaned, obj):
        if "code" in cleaned:
            _unique(ctx, Account, Account.code, cleaned["code"], exclude=obj.id if obj else None,
                    extra=(Account.tenant_id == ctx.tid,))

    def extra(self, ctx, obj, detail):
        return {"user_count": _count(ctx, User, User.account_id == obj.id),
                "ticket_count": _count(ctx, Ticket, Ticket.account_id == obj.id)}

    def guard_delete(self, ctx, obj):
        # Deleting an account cascades to its services, CIs and KB articles (and detaches users/tickets): refuse and
        # point at "inactive" instead.
        _in_use(users=_count(ctx, User, User.account_id == obj.id), tickets=_count(ctx, Ticket, Ticket.account_id == obj.id),
                services=_count(ctx, Service, Service.account_id == obj.id),
                configuration_items=_count(ctx, ConfigurationItem, ConfigurationItem.account_id == obj.id),
                knowledge_articles=_count(ctx, KnowledgeArticle, KnowledgeArticle.account_id == obj.id))


# ===================================================================================== channels
def _mask_config(cfg: dict) -> dict:
    return {k: (MASK if (isinstance(v, str) and v and SECRET_KEY_RE.search(k)) else v) for k, v in (cfg or {}).items()}


def _kind_check(v):
    if v not in adapter_classes():
        raise FieldError("Unknown channel kind. Registered: " + ", ".join(sorted(adapter_classes())))


class Channels(Resource):
    slug, label, model = "channels", "Channels", Channel
    search, order = ("name", "kind"), (Channel.name,)
    list_filters = {"kind": "kind", "is_active": "is_active"}
    fields = {"name": F("str", required=True, max_len=120), "kind": F("str", required=True, max_len=40, check=_kind_check),
              "is_active": F("bool", default=True),
              "config": F("json_dict", default=dict, help="Adapter credentials; secrets are encrypted and shown masked."),
              "settings": F("json_dict", default=dict, help="Routing defaults.")}

    def serialize_value(self, ctx, obj, name, spec):
        v = getattr(obj, name)
        return _mask_config(v) if name == "config" else v

    def diff_state(self, ctx, obj):
        d = super().diff_state(ctx, obj)
        d["config"] = sorted((obj.config or {}).keys())  # never record secret values
        return d

    def apply(self, ctx, obj, cleaned):
        if "config" in cleaned:
            old = dict(obj.config or {})
            merged = {}
            for k, v in cleaned["config"].items():
                merged[k] = old.get(k, "") if v == MASK else v  # a masked value means "unchanged"
            cleaned = {**cleaned, "config": merged}
        super().apply(ctx, obj, cleaned)

    def extra(self, ctx, obj, detail):
        from ..settings import settings
        out = {"webhook_url": f"{settings.public_base_url}/api/channels/inbound/{obj.inbound_token}/",
               "created_at": obj.created_at.isoformat() if obj.created_at else None,
               "oauth_status": ((obj.config or {}).get("oauth_status") or
                                ("active" if (obj.config or {}).get("auth_type") == "oauth2" else None))
               if obj.kind == "imap" else None}
        if detail:
            out["inbound_counts"] = {st: n for st, n in ctx.s.execute(
                select(InboundMessage.status, func.count()).where(InboundMessage.channel_id == obj.id)
                .group_by(InboundMessage.status))}
        return out

    def guard_delete(self, ctx, obj):
        _in_use(tickets=_count(ctx, Ticket, Ticket.channel_id == obj.id))


# ===================================================================================== automation rules
def _check_conditions(v):
    for i, c in enumerate(v):
        if not isinstance(c, dict) or not isinstance(c.get("field", ""), str) or not str(c.get("field", "")).strip():
            raise FieldError(f"Condition {i + 1}: choose a field.")
        if len(c["field"]) > 100:
            raise FieldError(f"Condition {i + 1}: the field name is too long.")
        if c.get("op", "eq") not in CONDITION_OPS:
            raise FieldError(f"Condition {i + 1}: unknown operator.")
        if c.get("op") in ("in", "not_in") and not isinstance(c.get("value"), list):
            raise FieldError(f"Condition {i + 1}: this operator needs a list of values.")


def _check_actions(v):
    if not v:
        raise FieldError("Add at least one action.")
    for i, a in enumerate(v):
        n = f"Action {i + 1}"
        if not isinstance(a, dict) or a.get("type") not in ACTION_TYPES:
            raise FieldError(f"{n}: unknown action type.")
        t = a["type"]
        if t in ("assign_team", "assign_user", "set_priority", "add_tag", "transition_to", "add_note"):
            if not isinstance(a.get("value"), str) or not a["value"].strip():
                raise FieldError(f"{n}: a value is required.")
        elif t == "set_major_incident":
            if not isinstance(a.get("value", True), bool):
                raise FieldError(f"{n}: the value must be true or false.")
        elif t == "set_field":
            if not isinstance(a.get("field"), str) or not a["field"]:
                raise FieldError(f"{n}: choose a field.")
        elif t == "notify":
            to = a.get("to")
            if not isinstance(to, list) or not to or not all(isinstance(x, str) for x in to):
                raise FieldError(f"{n}: choose who to notify.")
        elif t == "webhook":
            url = a.get("url")
            if not isinstance(url, str) or not re.match(r"^https?://", url):
                raise FieldError(f"{n}: the URL must start with http:// or https://.")


class Rules(Resource):
    slug, label, model = "rules", "Automation rules", Rule
    search, order = ("name",), (Rule.order, Rule.id)
    list_filters = {"event": "event", "is_active": "is_active"}
    fields = {"name": F("str", required=True, max_len=120), "is_active": F("bool", default=True),
              "event": F("choice", required=True, choices=[e for e in RULE_EVENTS]),
              "conditions": F("json_list", check=_check_conditions, default=list),
              "actions": F("json_list", required=True, check=_check_actions),
              "order": F("int", min=0, max=100000, default=0), "stop_processing": F("bool", default=False)}

    def extra(self, ctx, obj, detail):
        return {"run_count": obj.run_count, "last_run_at": obj.last_run_at.isoformat() if obj.last_run_at else None}


# ===================================================================================== CMDB
class Services(Resource):
    slug, label, model = "services", "Services", Service
    search, order = ("name",), (Service.name,)
    list_filters = {"account_id": "account_id", "criticality": "criticality", "is_active": "is_active"}
    fields = {"name": F("str", required=True, max_len=200), "description": F("text", default=""),
              "account_id": F("fk", nullable=True, model=Account, default=None, help="Empty = shared service."),
              "owner_team_id": F("fk", nullable=True, model=Team, default=None),
              "criticality": F("int", min=1, max=4, default=3), "is_active": F("bool", default=True)}

    def extra(self, ctx, obj, detail):
        return {"ci_count": _count(ctx, ConfigurationItem, ConfigurationItem.service_id == obj.id)}


class ConfigurationItems(Resource):
    slug, label, model = "items", "Configuration items", ConfigurationItem
    search, order = ("name", "tech_stack"), (ConfigurationItem.name,)
    list_filters = {"ci_type": "ci_type", "environment": "environment", "status": "status", "account_id": "account_id"}
    fields = {"name": F("str", required=True, max_len=200), "ci_type": F("choice", choices=CI_TYPES, default="application"),
              "environment": F("choice", choices=ENVIRONMENTS, default="prod"),
              "tech_stack": F("str", max_len=200, default=""), "version": F("str", max_len=60, default=""),
              "status": F("choice", choices=CI_STATUSES, default="active"),
              "account_id": F("fk", nullable=True, model=Account, default=None),
              "service_id": F("fk", nullable=True, model=Service, default=None),
              "owner_team_id": F("fk", nullable=True, model=Team, default=None),
              "attributes": F("json_dict", default=dict),
              "depends_on": F("id_list", model=ConfigurationItem, default=list, attr="_depends_on")}

    def serialize_value(self, ctx, obj, name, spec):
        if name == "depends_on":
            return sorted(ctx.s.scalars(select(CIDependency.to_configurationitem_id).where(
                CIDependency.from_configurationitem_id == obj.id)))
        return getattr(obj, name)

    def apply(self, ctx, obj, cleaned):
        super().apply(ctx, obj, {k: v for k, v in cleaned.items() if k != "depends_on"})

    def validate(self, ctx, cleaned, obj):
        deps = cleaned.get("depends_on")
        if deps and obj is not None and obj.id in deps:
            raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {"depends_on": "An item cannot depend on itself."}})

    def after_save(self, ctx, obj, cleaned, created):
        if "depends_on" in cleaned:
            ctx.s.execute(delete(CIDependency).where(CIDependency.from_configurationitem_id == obj.id))
            ctx.s.add_all([CIDependency(from_configurationitem_id=obj.id, to_configurationitem_id=i)
                           for i in cleaned["depends_on"] if i != obj.id])
            ctx.s.flush()

    def extra(self, ctx, obj, detail):
        return {"type_label": obj.type_label}


# ===================================================================================== service desk content
class CannedResponses(Resource):
    slug, label, model = "canned-responses", "Canned responses", CannedResponse
    search, order = ("title",), (CannedResponse.title,)
    fields = {"title": F("str", required=True, max_len=120),
              "body": F("text", required=True, help="Placeholders: {{ticket.number}}, {{ticket.subject}}, "
                                                    "{{requester.first_name}}, {{agent.first_name}}"),
              "ticket_type_id": F("fk", nullable=True, model=TicketType, default=None),
              "owner_id": F("fk", nullable=True, model=User, label="email", default=None,
                            scope=lambda stmt, s, tid: stmt.where(User.role.in_(STAFF_ROLES), User.is_active.is_(True)),
                            help="Empty = shared with all agents.")}


def _check_form_schema(v):
    for i, f in enumerate(v):
        n = f"Field {i + 1}"
        if not isinstance(f, dict):
            raise FieldError(f"{n}: must be an object.")
        key = f.get("name") or f.get("key")
        if not isinstance(key, str) or not key.strip():
            raise FieldError(f"{n}: a name is required.")
        if f.get("type", "text") not in ("text", "textarea", "number", "select", "boolean", "date"):
            raise FieldError(f"{n}: unknown field type.")
        if f.get("type") == "select" and not (isinstance(f.get("options"), list) and f["options"]):
            raise FieldError(f"{n}: a select field needs options.")


class CatalogItems(Resource):
    slug, label, model = "catalog-items", "Service catalog", ServiceCatalogItem
    search, order = ("name",), (ServiceCatalogItem.order, ServiceCatalogItem.name)
    fields = {"name": F("str", required=True, max_len=120), "description": F("text", default=""),
              "icon": F("str", max_len=60, default="box"),
              "category_id": F("fk", nullable=True, model=Category, default=None),
              "target_team_id": F("fk", nullable=True, model=Team, default=None),
              "ticket_type_id": F("fk", required=True, model=TicketType),
              "requires_approval": F("bool", default=True),
              "approval_type": F("choice", choices=("manager", "dept_head", "none"), default="manager"),
              "form_schema": F("json_list", check=_check_form_schema, default=list),
              "is_active": F("bool", default=True), "order": F("int", min=0, max=100000, default=0)}


class ContactIdentities(Resource):
    slug, label, model = "contact-identities", "Contact identities", ContactIdentity
    search, order = ("value", "kind"), (ContactIdentity.kind, ContactIdentity.value)
    fields = {"user_id": F("fk", required=True, model=User, label="email"), "kind": F("str", required=True, max_len=30, lower=True),
              "value": F("str", required=True, max_len=255)}

    def validate(self, ctx, cleaned, obj):
        kind = cleaned.get("kind", obj.kind if obj is not None else None)
        value = cleaned.get("value", obj.value if obj is not None else None)
        if kind == "email" and value:
            cleaned["value"] = value.lower()
            value = cleaned["value"]
        if kind is not None and value is not None:
            stmt = select(ContactIdentity.id).where(ContactIdentity.tenant_id == ctx.tid, ContactIdentity.kind == kind,
                                                    ContactIdentity.value == value)
            if obj is not None:
                stmt = stmt.where(ContactIdentity.id != obj.id)
            if ctx.s.scalars(stmt.limit(1)).first():
                raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": {"value": "This identity is already mapped."}})


RESOURCES: dict[str, Resource] = {r.slug: r for r in (
    Workflows(), States(), Transitions(), TicketTypes(), CustomFields(), Priorities(), Categories(), Accounts(),
    Channels(), Rules(), Services(), ConfigurationItems(), CannedResponses(), CatalogItems(), ContactIdentities())}
