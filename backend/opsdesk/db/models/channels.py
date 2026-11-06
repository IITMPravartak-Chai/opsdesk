from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, EncryptedJSON, TenantOwned, created_column, pk_column

INBOUND_STATUSES = ("queued", "processed", "duplicate", "ignored", "failed")


def new_inbound_token() -> str:
    return secrets.token_urlsafe(24)


class Channel(TenantOwned, Base):
    """A configured intake/delivery channel. ``kind`` selects the adapter class registered in
    ``settings.channel_adapters``, so new channels are plug-ins."""

    __tablename__ = "channels_channel"

    id: Mapped[int] = pk_column()
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)  # adapter credentials, encrypted at rest
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)  # routing defaults
    inbound_token: Mapped[str] = mapped_column(String(64), unique=True, default=new_inbound_token)
    created_at: Mapped[datetime] = created_column()

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"

    @property
    def adapter(self):
        from ...channels.registry import get_adapter
        return get_adapter(self)


class InboundMessage(TenantOwned, Base):
    """Durable outbox row for every inbound provider message (idempotent on channel + external_id)."""

    __tablename__ = "channels_inboundmessage"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_id", name="channels_inboundmessage_channel_id_external_id_uniq"),
        Index("channels_inbound_retry_idx", "status", "next_attempt_at"),
    )

    id: Mapped[int] = pk_column()
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels_channel.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    envelope: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    # Safe failure classification only ("transient:OperationalError"); never payload text or secrets.
    error: Mapped[str] = mapped_column(Text, default="")
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tickets_ticket.id", ondelete="SET NULL"), nullable=True, index=True)
    received_at: Mapped[datetime] = created_column(index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel = relationship("Channel", foreign_keys="InboundMessage.channel_id")
    ticket = relationship("Ticket", foreign_keys="InboundMessage.ticket_id")
