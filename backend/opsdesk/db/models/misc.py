"""Knowledge base, AI feedback and automation rules."""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantOwned, created_column, pk_column, updated_column

EMBEDDING_DIM = 384

RULE_EVENTS = ("ticket.created", "ticket.updated", "ticket.state_changed", "ticket.customer_replied",
               "sla.at_risk", "sla.breached")


class KnowledgeArticle(TenantOwned, Base):
    __tablename__ = "kb_knowledgearticle"
    __table_args__ = (
        Index("kb_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    id: Mapped[int] = pk_column()
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)  # Markdown
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_category.id", ondelete="SET NULL"), nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_account.id", ondelete="CASCADE"), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(10), default="internal")  # internal | public
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | published | retired
    tags: Mapped[list] = mapped_column(ARRAY(String(50)), default=list)
    source_ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tickets_ticket.id", ondelete="SET NULL"), nullable=True, index=True)
    author_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    helpful_votes: Mapped[int] = mapped_column(Integer, default=0)
    unhelpful_votes: Mapped[int] = mapped_column(Integer, default=0)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = created_column()
    updated_at: Mapped[datetime] = updated_column()

    author = relationship("User", foreign_keys="KnowledgeArticle.author_id")


class AIFeedback(TenantOwned, Base):
    """Agent accept/reject signal on AI suggestions, used to measure precision per feature before
    turning on automatic application."""

    __tablename__ = "ai_aifeedback"

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tickets_ticket.id", ondelete="CASCADE"), nullable=True, index=True)
    feature: Mapped[str] = mapped_column(String(20))  # triage similar kb summary draft duplicate
    suggestion: Mapped[dict] = mapped_column(JSONB, default=dict)
    accepted: Mapped[bool] = mapped_column(Boolean)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = created_column()


class Rule(TenantOwned, Base):
    """If <event> and all <conditions> match, run <actions>."""

    __tablename__ = "automation_rule"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    event: Mapped[str] = mapped_column(String(40))
    conditions: Mapped[list] = mapped_column(JSONB, default=list)
    actions: Mapped[list] = mapped_column(JSONB, default=list)
    order: Mapped[int] = mapped_column(Integer, default=0)
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
