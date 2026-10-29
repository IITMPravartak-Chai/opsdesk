from __future__ import annotations

import secrets
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String,
                        Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantOwned, created_column, pk_column, updated_column
from .misc import EMBEDDING_DIM

SOURCES = ("portal", "mobile", "email", "whatsapp", "webhook", "phone", "api", "other")
SLA_STATUSES = ("on_track", "at_risk", "breached", "paused", "met", "none")
MAJOR_INCIDENT_STATUSES = ("investigating", "identified", "monitoring", "resolved")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def attachment_path(tenant_id: int, ticket_id: int, filename: str) -> str:
    return f"t{tenant_id}/tickets/{ticket_id}/{secrets.token_hex(8)}-{filename}"


class TicketSequence(TenantOwned, Base):
    __tablename__ = "tickets_ticketsequence"
    __table_args__ = (UniqueConstraint("tenant_id", "prefix", name="tickets_ticketsequence_tenant_id_prefix_uniq"),)

    id: Mapped[int] = pk_column()
    prefix: Mapped[str] = mapped_column(String(6))
    last_value: Mapped[int] = mapped_column(BigInteger, default=0)


class Ticket(TenantOwned, Base):
    __tablename__ = "tickets_ticket"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="tickets_ticket_tenant_id_number_uniq"),
        Index("tickets_ticket_tenant_id_state_id_idx", "tenant_id", "state_id"),
        Index("tickets_ticket_tenant_id_assignee_id_idx", "tenant_id", "assignee_id"),
        Index("tickets_ticket_tenant_id_account_id_idx", "tenant_id", "account_id"),
        Index("tickets_ticket_tenant_id_sla_status_idx", "tenant_id", "sla_status"),
        Index("ticket_fts_idx",
              text("to_tsvector('english'::regconfig, COALESCE(subject, '') || ' ' || COALESCE(description, ''))"),
              postgresql_using="gin"),
        Index("ticket_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = pk_column()
    number: Mapped[str] = mapped_column(String(20))
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_tickettype.id", ondelete="RESTRICT"), index=True)
    state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_state.id", ondelete="RESTRICT"), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")

    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_account.id", ondelete="SET NULL"), nullable=True, index=True)
    requester_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_team.id", ondelete="SET NULL"), nullable=True, index=True)

    category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("workflow_category.id", ondelete="SET NULL"), nullable=True, index=True)
    service_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cmdb_service.id", ondelete="SET NULL"), nullable=True, index=True)
    ci_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cmdb_configurationitem.id", ondelete="SET NULL"), nullable=True, index=True)
    impact: Mapped[int] = mapped_column(SmallInteger, default=3)
    urgency: Mapped[int] = mapped_column(SmallInteger, default=3)
    priority_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("workflow_priority.id", ondelete="SET NULL"), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(20), default="portal")
    channel_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("channels_channel.id", ondelete="SET NULL"), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(ARRAY(String(50)), default=list)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)

    is_major_incident: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_commander_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    major_incident_status: Mapped[str] = mapped_column(String(30), default="")
    post_incident_review: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="SET NULL"), nullable=True, index=True)

    # SLA
    sla_policy_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("workflow_slapolicy.id", ondelete="SET NULL"), nullable=True, index=True)
    sla_status: Mapped[str] = mapped_column(String(12), default="none")
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_breached: Mapped[bool] = mapped_column(Boolean, default=False)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_code: Mapped[str] = mapped_column(String(60), default="")
    resolution_notes: Mapped[str] = mapped_column(Text, default="")

    # AI
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_triage: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = created_column(index=True)
    updated_at: Mapped[datetime] = updated_column()

    tenant = relationship("Tenant", foreign_keys="Ticket.tenant_id", viewonly=True)
    state = relationship("State")
    ticket_type = relationship("TicketType")
    priority = relationship("Priority")
    account = relationship("Account", foreign_keys="Ticket.account_id")
    requester = relationship("User", foreign_keys="Ticket.requester_id")
    created_by = relationship("User", foreign_keys="Ticket.created_by_id")
    assignee = relationship("User", foreign_keys="Ticket.assignee_id")
    incident_commander = relationship("User", foreign_keys="Ticket.incident_commander_id")
    team = relationship("Team", foreign_keys="Ticket.team_id")
    category = relationship("Category", foreign_keys="Ticket.category_id")
    service = relationship("Service", foreign_keys="Ticket.service_id")
    ci = relationship("ConfigurationItem", foreign_keys="Ticket.ci_id")
    channel = relationship("Channel", foreign_keys="Ticket.channel_id")
    sla_policy = relationship("SLAPolicy", foreign_keys="Ticket.sla_policy_id")
    parent = relationship("Ticket", remote_side="Ticket.id", foreign_keys="Ticket.parent_id")
    watchers = relationship("User", secondary="tickets_ticket_watchers", lazy="select")
    approvals = relationship("Approval", back_populates="ticket")
    csat = relationship("CSATSurvey", uselist=False, back_populates="ticket")


class TicketWatcher(Base):
    __tablename__ = "tickets_ticket_watchers"
    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="tickets_ticket_watchers_ticket_id_user_id_uniq"),)

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)


class Interaction(TenantOwned, Base):
    """One entry in the ticket conversation: a public reply, an internal note or a system message."""

    __tablename__ = "tickets_interaction"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="public")  # public internal system
    direction: Mapped[str] = mapped_column(String(10), default="internal")  # inbound outbound internal
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="portal")
    channel_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("channels_channel.id", ondelete="SET NULL"), nullable=True, index=True)
    external_message_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    delivery_status: Mapped[str] = mapped_column(String(10), default="n/a")
    delivery_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_column(index=True)

    author = relationship("User", foreign_keys="Interaction.author_id")
    ticket = relationship("Ticket", foreign_keys="Interaction.ticket_id")


class Attachment(TenantOwned, Base):
    __tablename__ = "tickets_attachment"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    interaction_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tickets_interaction.id", ondelete="SET NULL"), nullable=True, index=True)
    file: Mapped[str] = mapped_column(String(500))  # storage key
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    scan_status: Mapped[str] = mapped_column(String(10), default="pending")  # pending clean infected skipped
    uploaded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = created_column()

    interaction = relationship("Interaction", foreign_keys="Attachment.interaction_id")


class TicketEvent(TenantOwned, Base):
    """Immutable history of everything that happened to a ticket."""

    __tablename__ = "tickets_ticketevent"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_column(index=True)

    actor = relationship("User", foreign_keys="TicketEvent.actor_id")


class TicketLink(TenantOwned, Base):
    __tablename__ = "tickets_ticketlink"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "link_type", name="tickets_ticketlink_source_id_target_id_link_type_uniq"),)

    id: Mapped[int] = pk_column()
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    link_type: Mapped[str] = mapped_column(String(20), default="related")  # related duplicate_of caused_by blocks
    created_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = created_column()


class Approval(TenantOwned, Base):
    __tablename__ = "tickets_approval"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    approver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending approved rejected
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket = relationship("Ticket", back_populates="approvals")
    approver = relationship("User", foreign_keys="Approval.approver_id")
    requested_by = relationship("User", foreign_keys="Approval.requested_by_id")


class TimeEntry(TenantOwned, Base):
    __tablename__ = "tickets_timeentry"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    billable: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    work_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = created_column()


class CannedResponse(TenantOwned, Base):
    __tablename__ = "tickets_cannedresponse"

    id: Mapped[int] = pk_column()
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    ticket_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("workflow_tickettype.id", ondelete="CASCADE"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), nullable=True, index=True)


class CSATSurvey(TenantOwned, Base):
    __tablename__ = "tickets_csatsurvey"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), unique=True)
    token: Mapped[str] = mapped_column(String(48), unique=True, default=new_token)
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = created_column()
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket = relationship("Ticket", back_populates="csat")


class ServiceCatalogItem(TenantOwned, Base):
    """An internal service offering presented in the employee service catalog."""

    __tablename__ = "tickets_servicecatalogitem"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(60), default="box")
    category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("workflow_category.id", ondelete="SET NULL"), nullable=True, index=True)
    target_team_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_team.id", ondelete="SET NULL"), nullable=True, index=True)
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workflow_tickettype.id", ondelete="RESTRICT"), index=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_type: Mapped[str] = mapped_column(String(20), default="manager")  # manager dept_head none
    form_schema: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_column()

    category = relationship("Category", foreign_keys="ServiceCatalogItem.category_id")
    target_team = relationship("Team", foreign_keys="ServiceCatalogItem.target_team_id")
    ticket_type = relationship("TicketType", foreign_keys="ServiceCatalogItem.ticket_type_id")


class SavedQueue(TenantOwned, Base):
    """A personalised or shared queue filter configuration saved by an agent or manager."""

    __tablename__ = "tickets_savedqueue"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "name", name="tickets_savedqueue_tenant_id_user_id_name_uniq"),)

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), nullable=True, index=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()


class Notification(TenantOwned, Base):
    """In-app notification for events requiring user attention."""

    __tablename__ = "tickets_notification"
    __table_args__ = (Index("tickets_not_tenant__74c207_idx", "tenant_id", "recipient_id", "is_read"),)

    id: Mapped[int] = pk_column()
    recipient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500), default="")
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), nullable=True, index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = created_column(index=True)

    recipient = relationship("User", foreign_keys="Notification.recipient_id")
    actor = relationship("User", foreign_keys="Notification.actor_id")
    ticket = relationship("Ticket", foreign_keys="Notification.ticket_id")


class NotificationPreference(TenantOwned, Base):
    """User-specific notification settings controlling channels and event types."""

    __tablename__ = "tickets_notificationpreference"

    id: Mapped[int] = pk_column()
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), unique=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_assignment: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_replies: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_approvals: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_sla: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()
