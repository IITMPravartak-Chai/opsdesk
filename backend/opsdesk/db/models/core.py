from __future__ import annotations

from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TenantOwned, created_column, pk_column, utcnow

TIERS = ("platinum", "gold", "silver", "standard")
ROLES = ("super_admin", "manager", "team_lead", "employee", "customer")
STAFF_ROLES = ("super_admin", "manager", "team_lead", "employee")
TOP_ROLES = ("super_admin", "manager")                      # full access: SLA, audit log, passwords, role changes
ADMIN_ROLES = ("super_admin",)                               # Administration: users, teams, SLA, configuration, audit log
PRIVILEGED_ROLES = ("super_admin", "manager", "team_lead")  # see every ticket; manage users, teams and configuration
OWN_TICKET_ROLES = ("employee",)                             # see only tickets assigned to them or raised by them


def effective_roles(role: str) -> tuple:
    """Role names a user counts as when a workflow transition lists ``allowed_roles``. A super admin is a manager
    there until it gets transitions of its own."""
    return ("super_admin", "manager") if role == "super_admin" else (role,)
STRATEGIES = ("manual", "round_robin", "least_loaded")


class Tenant(Base):
    """A service-provider workspace. Everything else hangs off a tenant and is isolated by RLS."""

    __tablename__ = "core_tenant"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Tenant-wide toggles: auto_close_days, customer_sees_account_tickets, auto_apply_triage,
    # triage_confidence_threshold, csat_enabled, brand.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_column()

    def setting(self, key: str, default=None):
        return (self.settings or {}).get(key, default)


class BusinessHours(TenantOwned, Base):
    """Support calendar used by SLA timers. ``schedule`` maps weekday (0=Mon) to [start, end] pairs."""

    __tablename__ = "core_businesshours"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_24x7: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[dict] = mapped_column(JSONB, default=dict)
    holidays: Mapped[list] = mapped_column(JSONB, default=list)


class Account(TenantOwned, Base):
    """A customer organisation you support."""

    __tablename__ = "core_account"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="core_account_tenant_id_code_uniq"),)

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(20))
    tier: Mapped[str] = mapped_column(String(20), default="standard")
    email_domains: Mapped[list] = mapped_column(JSONB, default=list)
    sla_policy_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workflow_slapolicy.id", ondelete="SET NULL"), nullable=True, index=True)
    business_hours_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_businesshours.id", ondelete="SET NULL"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    sla_policy = relationship("SLAPolicy", foreign_keys="Account.sla_policy_id")
    business_hours = relationship("BusinessHours", foreign_keys="Account.business_hours_id")


class Department(TenantOwned, Base):
    """An internal organisation unit (e.g. Engineering, Sales, People & HR)."""

    __tablename__ = "core_department"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="core_department_tenant_id_code_uniq"),)

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(20))
    head_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_user.id", ondelete="SET NULL", use_alter=True,
                               name="core_department_head_id_fk"),
        nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_column()

    head = relationship("User", foreign_keys="Department.head_id")


class User(Base):
    """Staff (managers, team leads, employees) and customers. Not RLS-protected: login happens before a tenant is known.

    Password hashes use the format in security/passwords.py."""

    __tablename__ = "core_user"
    __table_args__ = (
        # at most one super admin on the whole site (across every tenant)
        Index("core_user_single_super_admin", "role", unique=True, postgresql_where=text("role = 'super_admin'")),
    )

    id: Mapped[int] = pk_column()
    password: Mapped[str] = mapped_column(String(128))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    first_name: Mapped[str] = mapped_column(String(150), default="")
    last_name: Mapped[str] = mapped_column(String(150), default="")
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    date_joined: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_tenant.id", ondelete="CASCADE"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="customer")
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_account.id", ondelete="SET NULL"), nullable=True, index=True)
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_department.id", ondelete="SET NULL"), nullable=True, index=True)
    manager_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    job_title: Mapped[str] = mapped_column(String(120), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str] = mapped_column(String(64), default="")

    tenant = relationship("Tenant", foreign_keys="User.tenant_id")
    account = relationship("Account", foreign_keys="User.account_id")
    department = relationship("Department", foreign_keys="User.department_id")
    manager = relationship("User", remote_side="User.id", foreign_keys="User.manager_id")
    teams = relationship("Team", secondary="core_team_members", viewonly=True, order_by="Team.name")

    @property
    def is_agent(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_customer(self) -> bool:
        return self.role == "customer"

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.email


class ContactIdentity(TenantOwned, Base):
    """Maps a channel identity (email address, WhatsApp number...) to a user."""

    __tablename__ = "core_contactidentity"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "value", name="core_contactidentity_tenant_id_kind_value_uniq"),)

    id: Mapped[int] = pk_column()
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(String(255))

    user = relationship("User", foreign_keys="ContactIdentity.user_id")


class Team(TenantOwned, Base):
    __tablename__ = "core_team"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), default="")
    assignment_strategy: Mapped[str] = mapped_column(String(20), default="manual")
    rr_cursor: Mapped[int] = mapped_column(Integer, default=0)
    team_lead_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    manager_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)

    team_lead = relationship("User", foreign_keys="Team.team_lead_id")
    manager = relationship("User", foreign_keys="Team.manager_id")
    members = relationship("User", secondary="core_team_members", lazy="select", viewonly=False)
    accounts = relationship("Account", secondary="core_team_accounts", lazy="select", viewonly=False)


class TeamMember(Base):
    __tablename__ = "core_team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="core_team_members_team_id_user_id_uniq"),)

    id: Mapped[int] = pk_column()
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_team.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_user.id", ondelete="CASCADE"), index=True)


class TeamAccount(Base):
    __tablename__ = "core_team_accounts"
    __table_args__ = (UniqueConstraint("team_id", "account_id", name="core_team_accounts_team_id_account_id_uniq"),)

    id: Mapped[int] = pk_column()
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_team.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("core_account.id", ondelete="CASCADE"), index=True)


class AuthBlacklistedToken(Base):
    """Revoked refresh-token ids. Not tenant scoped (the jti is
    an unguessable random id); rows are purged after ``expires_at``."""

    __tablename__ = "auth_blacklistedtoken"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = created_column()


class AuditLog(TenantOwned, Base):
    """Append-only for UPDATE and DELETE (trigger ``audit_immutable``, installed by the RLS migration).
    The trigger does not fire on TRUNCATE and the table owner can disable it, so this is not
    tamper-proof storage."""

    __tablename__ = "core_auditlog"

    id: Mapped[int] = pk_column()
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("core_user.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80))
    object_type: Mapped[str] = mapped_column(String(80), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    changes: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = created_column(index=True)

    actor = relationship("User", foreign_keys="AuditLog.actor_id")
