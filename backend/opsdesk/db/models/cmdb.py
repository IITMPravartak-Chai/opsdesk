from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantOwned, pk_column

CI_TYPES = ("application", "ai_model", "llm_app", "data_pipeline", "platform", "database",
            "infrastructure", "integration", "other")
ENVIRONMENTS = ("prod", "staging", "dev")
CI_STATUSES = ("active", "maintenance", "retired")


class Service(TenantOwned, Base):
    """A business or technical service delivered to one account (or shared)."""

    __tablename__ = "cmdb_service"

    id: Mapped[int] = pk_column()
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_account.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_team.id", ondelete="SET NULL"), nullable=True, index=True)
    criticality: Mapped[int] = mapped_column(SmallInteger, default=3)  # 1 mission critical .. 4 low
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    account = relationship("Account", foreign_keys="Service.account_id")
    owner_team = relationship("Team", foreign_keys="Service.owner_team_id")


class ConfigurationItem(TenantOwned, Base):
    """Anything you support: applications, AI models, pipelines, platforms. AI-specific metadata
    (provider, version, eval metrics, drift thresholds) lives in ``attributes``."""

    __tablename__ = "cmdb_configurationitem"

    id: Mapped[int] = pk_column()
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_account.id", ondelete="CASCADE"), nullable=True, index=True)
    service_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cmdb_service.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    ci_type: Mapped[str] = mapped_column(String(30), default="application")
    environment: Mapped[str] = mapped_column(String(10), default="prod")
    tech_stack: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(60), default="")
    owner_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_team.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    account = relationship("Account", foreign_keys="ConfigurationItem.account_id")
    service = relationship("Service", foreign_keys="ConfigurationItem.service_id")
    owner_team = relationship("Team", foreign_keys="ConfigurationItem.owner_team_id")


class CIDependency(Base):
    """``from`` depends on ``to`` (asymmetric self many-to-many ``depends_on``)."""

    __tablename__ = "cmdb_configurationitem_depends_on"
    __table_args__ = (UniqueConstraint("from_configurationitem_id", "to_configurationitem_id",
                                       name="cmdb_configurationitem_depends_on_uniq"),)

    id: Mapped[int] = pk_column()
    from_configurationitem_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_configurationitem.id", ondelete="CASCADE"), index=True)
    to_configurationitem_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cmdb_configurationitem.id", ondelete="CASCADE"), index=True)
