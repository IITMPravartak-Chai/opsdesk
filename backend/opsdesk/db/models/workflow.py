"""Everything about *how* tickets move is data, not code: statuses, transitions, who may perform
them, required fields, approvals, SLA targets and the impact x urgency priority matrix."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantOwned, pk_column

STATE_CATEGORIES = ("new", "open", "pending", "resolved", "closed", "cancelled")
FIELD_TYPES = ("text", "textarea", "number", "select", "multiselect", "date", "boolean")
IMPACT_LABELS = {1: "Enterprise-wide", 2: "Department / many users", 3: "Team / few users", 4: "Single user"}
URGENCY_LABELS = {1: "Critical - work stopped", 2: "High", 3: "Medium", 4: "Low"}


class Workflow(TenantOwned, Base):
    __tablename__ = "workflow_workflow"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    customer_reply_state_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_state.id", ondelete="SET NULL", use_alter=True,
                               name="workflow_workflow_customer_reply_state_id_fk"),
        nullable=True, index=True)

    states = relationship("State", back_populates="workflow", foreign_keys="State.workflow_id",
                          order_by="(State.order, State.id)", cascade="all, delete-orphan", passive_deletes=True)
    transitions = relationship("Transition", back_populates="workflow", order_by="(Transition.order, Transition.id)",
                               cascade="all, delete-orphan", passive_deletes=True)


class State(Base):
    __tablename__ = "workflow_state"
    __table_args__ = (UniqueConstraint("workflow_id", "key", name="workflow_state_workflow_id_key_uniq"),)

    id: Mapped[int] = pk_column()
    workflow_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_workflow.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20), default="open")
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False)
    pauses_sla: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str] = mapped_column(String(7), default="#64748b")

    workflow = relationship("Workflow", back_populates="states", foreign_keys=[workflow_id])

    @property
    def is_final(self) -> bool:
        return self.category in ("closed", "cancelled")

    @property
    def is_done(self) -> bool:
        return self.category in ("resolved", "closed", "cancelled")


class Transition(Base):
    __tablename__ = "workflow_transition"

    id: Mapped[int] = pk_column()
    workflow_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    from_state_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_state.id", ondelete="CASCADE"), nullable=True, index=True)
    to_state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_state.id", ondelete="CASCADE"), index=True)
    allowed_roles: Mapped[list] = mapped_column(JSONB, default=list)
    customer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    required_fields: Mapped[list] = mapped_column(JSONB, default=list)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)

    workflow = relationship("Workflow", back_populates="transitions")
    from_state = relationship("State", foreign_keys=[from_state_id])
    to_state = relationship("State", foreign_keys=[to_state_id])


class TicketType(TenantOwned, Base):
    __tablename__ = "workflow_tickettype"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="workflow_tickettype_tenant_id_key_uniq"),)

    id: Mapped[int] = pk_column()
    key: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(6))
    workflow_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_workflow.id", ondelete="RESTRICT"), index=True)
    default_impact: Mapped[int] = mapped_column(SmallInteger, default=3)
    default_urgency: Mapped[int] = mapped_column(SmallInteger, default=3)
    customer_can_create: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=0)

    workflow = relationship("Workflow")
    custom_fields = relationship("CustomField", back_populates="ticket_type", order_by="(CustomField.order, CustomField.id)",
                                 cascade="all, delete-orphan", passive_deletes=True)


class CustomField(TenantOwned, Base):
    __tablename__ = "workflow_customfield"
    __table_args__ = (UniqueConstraint("ticket_type_id", "key", name="workflow_customfield_ticket_type_id_key_uniq"),)

    id: Mapped[int] = pk_column()
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_tickettype.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(120))
    field_type: Mapped[str] = mapped_column(String(20), default="text")
    options: Mapped[list] = mapped_column(JSONB, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    visible_to_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    help_text: Mapped[str] = mapped_column(String(200), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)

    ticket_type = relationship("TicketType", back_populates="custom_fields")


class Priority(TenantOwned, Base):
    __tablename__ = "workflow_priority"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="workflow_priority_tenant_id_key_uniq"),)

    id: Mapped[int] = pk_column()
    key: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(60))
    level: Mapped[int] = mapped_column(SmallInteger)  # 1 = most urgent
    color: Mapped[str] = mapped_column(String(7), default="#64748b")


class PriorityMatrix(TenantOwned, Base):
    """ITIL impact x urgency -> priority."""

    __tablename__ = "workflow_prioritymatrix"
    __table_args__ = (UniqueConstraint("tenant_id", "impact", "urgency", name="workflow_prioritymatrix_tenant_id_impact_urgency_uniq"),)

    id: Mapped[int] = pk_column()
    impact: Mapped[int] = mapped_column(SmallInteger)
    urgency: Mapped[int] = mapped_column(SmallInteger)
    priority_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_priority.id", ondelete="CASCADE"), index=True)

    priority = relationship("Priority")


class Category(TenantOwned, Base):
    __tablename__ = "workflow_category"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_category.id", ondelete="CASCADE"), nullable=True, index=True)
    ticket_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_tickettype.id", ondelete="CASCADE"), nullable=True, index=True)
    default_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_team.id", ondelete="SET NULL"), nullable=True, index=True)

    parent = relationship("Category", remote_side="Category.id", foreign_keys="Category.parent_id")
    ticket_type = relationship("TicketType", foreign_keys="Category.ticket_type_id")
    default_team = relationship("Team", foreign_keys="Category.default_team_id")


class SLAPolicy(TenantOwned, Base):
    __tablename__ = "workflow_slapolicy"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    business_hours_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_businesshours.id", ondelete="SET NULL"), nullable=True, index=True)
    at_risk_percent: Mapped[int] = mapped_column(SmallInteger, default=75)

    targets = relationship("SLATarget", back_populates="policy", cascade="all, delete-orphan", passive_deletes=True)
    business_hours = relationship("BusinessHours")
    ticket_types = relationship("TicketType", secondary="workflow_slapolicy_ticket_types", passive_deletes=True)


class SLAPolicyTicketType(Base):
    __tablename__ = "workflow_slapolicy_ticket_types"
    __table_args__ = (UniqueConstraint("slapolicy_id", "tickettype_id", name="workflow_slapolicy_ticket_types_uniq"),)

    id: Mapped[int] = pk_column()
    slapolicy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_slapolicy.id", ondelete="CASCADE"), index=True)
    tickettype_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_tickettype.id", ondelete="CASCADE"), index=True)


class SLATarget(Base):
    __tablename__ = "workflow_slatarget"
    __table_args__ = (UniqueConstraint("policy_id", "priority_id", name="workflow_slatarget_policy_id_priority_id_uniq"),)

    id: Mapped[int] = pk_column()
    policy_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_slapolicy.id", ondelete="CASCADE"), index=True)
    priority_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_priority.id", ondelete="CASCADE"), index=True)
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)

    policy = relationship("SLAPolicy", back_populates="targets")
    priority = relationship("Priority")
