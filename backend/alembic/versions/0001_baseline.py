"""baseline

Revision ID: 0001
Revises: 
Create Date: 2026-10-26 12:51:02.368171
"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create extensions, tables, indexes, SECURITY DEFINER functions, the signed tenant-context verifier and
    keystore, RLS policies, the audit trigger and grants."""
    from opsdesk.db import rls
    from opsdesk.settings import settings

    bind = op.get_bind()
    for ext in ("vector", "pg_trgm"):
        bind.exec_driver_sql(f"CREATE EXTENSION IF NOT EXISTS {ext}")
    bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    _create_schema()
    rls.install_functions(bind)
    key_id, secret_hex = settings.signing_key_id, settings.signing_key_secret
    bind.exec_driver_sql(rls.seed_signing_key_sql(key_id, secret_hex))
    rls.apply(bind)


def _create_schema() -> None:
    op.create_table('core_tenant',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=50), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('core_tenant_pkey')),
    sa.UniqueConstraint('slug', name=op.f('core_tenant_slug_uniq'))
    )
    op.create_table('automation_rule',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('event', sa.String(length=40), nullable=False),
    sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('actions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('stop_processing', sa.Boolean(), nullable=False),
    sa.Column('run_count', sa.Integer(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('automation_rule_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('automation_rule_pkey'))
    )
    op.create_index(op.f('automation_rule_tenant_id_idx'), 'automation_rule', ['tenant_id'], unique=False)
    op.create_table('channels_channel',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('config', sa.Text(), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inbound_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('channels_channel_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('channels_channel_pkey')),
    sa.UniqueConstraint('inbound_token', name=op.f('channels_channel_inbound_token_uniq'))
    )
    op.create_index(op.f('channels_channel_tenant_id_idx'), 'channels_channel', ['tenant_id'], unique=False)
    op.create_table('core_businesshours',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('is_24x7', sa.Boolean(), nullable=False),
    sa.Column('schedule', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('holidays', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_businesshours_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_businesshours_pkey'))
    )
    op.create_index(op.f('core_businesshours_tenant_id_idx'), 'core_businesshours', ['tenant_id'], unique=False)
    op.create_table('core_department',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('head_id', sa.BigInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_department_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_department_pkey')),
    sa.UniqueConstraint('tenant_id', 'code', name='core_department_tenant_id_code_uniq')
    )
    op.create_index(op.f('core_department_head_id_idx'), 'core_department', ['head_id'], unique=False)
    op.create_index(op.f('core_department_tenant_id_idx'), 'core_department', ['tenant_id'], unique=False)
    op.create_table('core_team',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('assignment_strategy', sa.String(length=20), nullable=False),
    sa.Column('rr_cursor', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_team_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_team_pkey'))
    )
    op.create_index(op.f('core_team_tenant_id_idx'), 'core_team', ['tenant_id'], unique=False)
    op.create_table('tickets_ticketsequence',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('prefix', sa.String(length=6), nullable=False),
    sa.Column('last_value', sa.BigInteger(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_ticketsequence_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_ticketsequence_pkey')),
    sa.UniqueConstraint('tenant_id', 'prefix', name='tickets_ticketsequence_tenant_id_prefix_uniq')
    )
    op.create_index(op.f('tickets_ticketsequence_tenant_id_idx'), 'tickets_ticketsequence', ['tenant_id'], unique=False)
    op.create_table('workflow_priority',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('key', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('color', sa.String(length=7), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_priority_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_priority_pkey')),
    sa.UniqueConstraint('tenant_id', 'key', name='workflow_priority_tenant_id_key_uniq')
    )
    op.create_index(op.f('workflow_priority_tenant_id_idx'), 'workflow_priority', ['tenant_id'], unique=False)
    op.create_table('workflow_workflow',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('customer_reply_state_id', sa.BigInteger(), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_workflow_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_workflow_pkey'))
    )
    op.create_index(op.f('workflow_workflow_customer_reply_state_id_idx'), 'workflow_workflow', ['customer_reply_state_id'], unique=False)
    op.create_index(op.f('workflow_workflow_tenant_id_idx'), 'workflow_workflow', ['tenant_id'], unique=False)
    op.create_table('workflow_prioritymatrix',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('impact', sa.SmallInteger(), nullable=False),
    sa.Column('urgency', sa.SmallInteger(), nullable=False),
    sa.Column('priority_id', sa.BigInteger(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['priority_id'], ['workflow_priority.id'], name=op.f('workflow_prioritymatrix_priority_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_prioritymatrix_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_prioritymatrix_pkey')),
    sa.UniqueConstraint('tenant_id', 'impact', 'urgency', name='workflow_prioritymatrix_tenant_id_impact_urgency_uniq')
    )
    op.create_index(op.f('workflow_prioritymatrix_priority_id_idx'), 'workflow_prioritymatrix', ['priority_id'], unique=False)
    op.create_index(op.f('workflow_prioritymatrix_tenant_id_idx'), 'workflow_prioritymatrix', ['tenant_id'], unique=False)
    op.create_table('workflow_slapolicy',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('business_hours_id', sa.BigInteger(), nullable=True),
    sa.Column('at_risk_percent', sa.SmallInteger(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['business_hours_id'], ['core_businesshours.id'], name=op.f('workflow_slapolicy_business_hours_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_slapolicy_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_slapolicy_pkey'))
    )
    op.create_index(op.f('workflow_slapolicy_business_hours_id_idx'), 'workflow_slapolicy', ['business_hours_id'], unique=False)
    op.create_index(op.f('workflow_slapolicy_tenant_id_idx'), 'workflow_slapolicy', ['tenant_id'], unique=False)
    op.create_table('workflow_state',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('workflow_id', sa.BigInteger(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('is_initial', sa.Boolean(), nullable=False),
    sa.Column('pauses_sla', sa.Boolean(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('color', sa.String(length=7), nullable=False),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflow_workflow.id'], name=op.f('workflow_state_workflow_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_state_pkey')),
    sa.UniqueConstraint('workflow_id', 'key', name='workflow_state_workflow_id_key_uniq')
    )
    op.create_index(op.f('workflow_state_workflow_id_idx'), 'workflow_state', ['workflow_id'], unique=False)
    op.create_table('workflow_tickettype',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('prefix', sa.String(length=6), nullable=False),
    sa.Column('workflow_id', sa.BigInteger(), nullable=False),
    sa.Column('default_impact', sa.SmallInteger(), nullable=False),
    sa.Column('default_urgency', sa.SmallInteger(), nullable=False),
    sa.Column('customer_can_create', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_tickettype_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflow_workflow.id'], name=op.f('workflow_tickettype_workflow_id_fk'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_tickettype_pkey')),
    sa.UniqueConstraint('tenant_id', 'key', name='workflow_tickettype_tenant_id_key_uniq')
    )
    op.create_index(op.f('workflow_tickettype_tenant_id_idx'), 'workflow_tickettype', ['tenant_id'], unique=False)
    op.create_index(op.f('workflow_tickettype_workflow_id_idx'), 'workflow_tickettype', ['workflow_id'], unique=False)
    op.create_table('core_account',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('email_domains', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('sla_policy_id', sa.BigInteger(), nullable=True),
    sa.Column('business_hours_id', sa.BigInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['business_hours_id'], ['core_businesshours.id'], name=op.f('core_account_business_hours_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sla_policy_id'], ['workflow_slapolicy.id'], name=op.f('core_account_sla_policy_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_account_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_account_pkey')),
    sa.UniqueConstraint('tenant_id', 'code', name='core_account_tenant_id_code_uniq')
    )
    op.create_index(op.f('core_account_business_hours_id_idx'), 'core_account', ['business_hours_id'], unique=False)
    op.create_index(op.f('core_account_sla_policy_id_idx'), 'core_account', ['sla_policy_id'], unique=False)
    op.create_index(op.f('core_account_tenant_id_idx'), 'core_account', ['tenant_id'], unique=False)
    op.create_table('workflow_category',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('ticket_type_id', sa.BigInteger(), nullable=True),
    sa.Column('default_team_id', sa.BigInteger(), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['default_team_id'], ['core_team.id'], name=op.f('workflow_category_default_team_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['workflow_category.id'], name=op.f('workflow_category_parent_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_category_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_type_id'], ['workflow_tickettype.id'], name=op.f('workflow_category_ticket_type_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_category_pkey'))
    )
    op.create_index(op.f('workflow_category_default_team_id_idx'), 'workflow_category', ['default_team_id'], unique=False)
    op.create_index(op.f('workflow_category_parent_id_idx'), 'workflow_category', ['parent_id'], unique=False)
    op.create_index(op.f('workflow_category_tenant_id_idx'), 'workflow_category', ['tenant_id'], unique=False)
    op.create_index(op.f('workflow_category_ticket_type_id_idx'), 'workflow_category', ['ticket_type_id'], unique=False)
    op.create_table('workflow_customfield',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_type_id', sa.BigInteger(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('field_type', sa.String(length=20), nullable=False),
    sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('visible_to_customer', sa.Boolean(), nullable=False),
    sa.Column('help_text', sa.String(length=200), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('workflow_customfield_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_type_id'], ['workflow_tickettype.id'], name=op.f('workflow_customfield_ticket_type_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_customfield_pkey')),
    sa.UniqueConstraint('ticket_type_id', 'key', name='workflow_customfield_ticket_type_id_key_uniq')
    )
    op.create_index(op.f('workflow_customfield_tenant_id_idx'), 'workflow_customfield', ['tenant_id'], unique=False)
    op.create_index(op.f('workflow_customfield_ticket_type_id_idx'), 'workflow_customfield', ['ticket_type_id'], unique=False)
    op.create_table('workflow_slapolicy_ticket_types',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('slapolicy_id', sa.BigInteger(), nullable=False),
    sa.Column('tickettype_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['slapolicy_id'], ['workflow_slapolicy.id'], name=op.f('workflow_slapolicy_ticket_types_slapolicy_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tickettype_id'], ['workflow_tickettype.id'], name=op.f('workflow_slapolicy_ticket_types_tickettype_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_slapolicy_ticket_types_pkey')),
    sa.UniqueConstraint('slapolicy_id', 'tickettype_id', name='workflow_slapolicy_ticket_types_uniq')
    )
    op.create_index(op.f('workflow_slapolicy_ticket_types_slapolicy_id_idx'), 'workflow_slapolicy_ticket_types', ['slapolicy_id'], unique=False)
    op.create_index(op.f('workflow_slapolicy_ticket_types_tickettype_id_idx'), 'workflow_slapolicy_ticket_types', ['tickettype_id'], unique=False)
    op.create_table('workflow_slatarget',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('policy_id', sa.BigInteger(), nullable=False),
    sa.Column('priority_id', sa.BigInteger(), nullable=False),
    sa.Column('first_response_minutes', sa.Integer(), nullable=False),
    sa.Column('resolution_minutes', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['policy_id'], ['workflow_slapolicy.id'], name=op.f('workflow_slatarget_policy_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['priority_id'], ['workflow_priority.id'], name=op.f('workflow_slatarget_priority_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_slatarget_pkey')),
    sa.UniqueConstraint('policy_id', 'priority_id', name='workflow_slatarget_policy_id_priority_id_uniq')
    )
    op.create_index(op.f('workflow_slatarget_policy_id_idx'), 'workflow_slatarget', ['policy_id'], unique=False)
    op.create_index(op.f('workflow_slatarget_priority_id_idx'), 'workflow_slatarget', ['priority_id'], unique=False)
    op.create_table('workflow_transition',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('workflow_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('from_state_id', sa.BigInteger(), nullable=True),
    sa.Column('to_state_id', sa.BigInteger(), nullable=False),
    sa.Column('allowed_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('customer_allowed', sa.Boolean(), nullable=False),
    sa.Column('requires_comment', sa.Boolean(), nullable=False),
    sa.Column('required_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('requires_approval', sa.Boolean(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['from_state_id'], ['workflow_state.id'], name=op.f('workflow_transition_from_state_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_state_id'], ['workflow_state.id'], name=op.f('workflow_transition_to_state_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflow_workflow.id'], name=op.f('workflow_transition_workflow_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('workflow_transition_pkey'))
    )
    op.create_index(op.f('workflow_transition_from_state_id_idx'), 'workflow_transition', ['from_state_id'], unique=False)
    op.create_index(op.f('workflow_transition_to_state_id_idx'), 'workflow_transition', ['to_state_id'], unique=False)
    op.create_index(op.f('workflow_transition_workflow_id_idx'), 'workflow_transition', ['workflow_id'], unique=False)
    op.create_table('cmdb_service',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('owner_team_id', sa.BigInteger(), nullable=True),
    sa.Column('criticality', sa.SmallInteger(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('cmdb_service_account_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_team_id'], ['core_team.id'], name=op.f('cmdb_service_owner_team_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('cmdb_service_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('cmdb_service_pkey'))
    )
    op.create_index(op.f('cmdb_service_account_id_idx'), 'cmdb_service', ['account_id'], unique=False)
    op.create_index(op.f('cmdb_service_owner_team_id_idx'), 'cmdb_service', ['owner_team_id'], unique=False)
    op.create_index(op.f('cmdb_service_tenant_id_idx'), 'cmdb_service', ['tenant_id'], unique=False)
    op.create_table('core_team_accounts',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('team_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('core_team_accounts_account_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['core_team.id'], name=op.f('core_team_accounts_team_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_team_accounts_pkey')),
    sa.UniqueConstraint('team_id', 'account_id', name='core_team_accounts_team_id_account_id_uniq')
    )
    op.create_index(op.f('core_team_accounts_account_id_idx'), 'core_team_accounts', ['account_id'], unique=False)
    op.create_index(op.f('core_team_accounts_team_id_idx'), 'core_team_accounts', ['team_id'], unique=False)
    op.create_table('core_user',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('password', sa.String(length=128), nullable=False),
    sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_superuser', sa.Boolean(), nullable=False),
    sa.Column('username', sa.String(length=150), nullable=False),
    sa.Column('first_name', sa.String(length=150), nullable=False),
    sa.Column('last_name', sa.String(length=150), nullable=False),
    sa.Column('is_staff', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('date_joined', sa.DateTime(timezone=True), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('department_id', sa.BigInteger(), nullable=True),
    sa.Column('manager_id', sa.BigInteger(), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('job_title', sa.String(length=120), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('is_available', sa.Boolean(), nullable=False),
    sa.Column('skills', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('mfa_enabled', sa.Boolean(), nullable=False),
    sa.Column('mfa_secret', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('core_user_account_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['department_id'], ['core_department.id'], name=op.f('core_user_department_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['manager_id'], ['core_user.id'], name=op.f('core_user_manager_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_user_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_user_pkey')),
    sa.UniqueConstraint('email', name=op.f('core_user_email_uniq')),
    sa.UniqueConstraint('username', name=op.f('core_user_username_uniq'))
    )
    op.create_index(op.f('core_user_account_id_idx'), 'core_user', ['account_id'], unique=False)
    op.create_index(op.f('core_user_department_id_idx'), 'core_user', ['department_id'], unique=False)
    op.create_index(op.f('core_user_manager_id_idx'), 'core_user', ['manager_id'], unique=False)
    op.create_index(op.f('core_user_tenant_id_idx'), 'core_user', ['tenant_id'], unique=False)
    op.create_table('tickets_servicecatalogitem',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('icon', sa.String(length=60), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('target_team_id', sa.BigInteger(), nullable=True),
    sa.Column('ticket_type_id', sa.BigInteger(), nullable=False),
    sa.Column('requires_approval', sa.Boolean(), nullable=False),
    sa.Column('approval_type', sa.String(length=20), nullable=False),
    sa.Column('form_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['workflow_category.id'], name=op.f('tickets_servicecatalogitem_category_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_team_id'], ['core_team.id'], name=op.f('tickets_servicecatalogitem_target_team_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_servicecatalogitem_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_type_id'], ['workflow_tickettype.id'], name=op.f('tickets_servicecatalogitem_ticket_type_id_fk'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_servicecatalogitem_pkey'))
    )
    op.create_index(op.f('tickets_servicecatalogitem_category_id_idx'), 'tickets_servicecatalogitem', ['category_id'], unique=False)
    op.create_index(op.f('tickets_servicecatalogitem_target_team_id_idx'), 'tickets_servicecatalogitem', ['target_team_id'], unique=False)
    op.create_index(op.f('tickets_servicecatalogitem_tenant_id_idx'), 'tickets_servicecatalogitem', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_servicecatalogitem_ticket_type_id_idx'), 'tickets_servicecatalogitem', ['ticket_type_id'], unique=False)
    op.create_table('cmdb_configurationitem',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('service_id', sa.BigInteger(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('ci_type', sa.String(length=30), nullable=False),
    sa.Column('environment', sa.String(length=10), nullable=False),
    sa.Column('tech_stack', sa.String(length=200), nullable=False),
    sa.Column('version', sa.String(length=60), nullable=False),
    sa.Column('owner_team_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('cmdb_configurationitem_account_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_team_id'], ['core_team.id'], name=op.f('cmdb_configurationitem_owner_team_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['service_id'], ['cmdb_service.id'], name=op.f('cmdb_configurationitem_service_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('cmdb_configurationitem_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('cmdb_configurationitem_pkey'))
    )
    op.create_index(op.f('cmdb_configurationitem_account_id_idx'), 'cmdb_configurationitem', ['account_id'], unique=False)
    op.create_index(op.f('cmdb_configurationitem_owner_team_id_idx'), 'cmdb_configurationitem', ['owner_team_id'], unique=False)
    op.create_index(op.f('cmdb_configurationitem_service_id_idx'), 'cmdb_configurationitem', ['service_id'], unique=False)
    op.create_index(op.f('cmdb_configurationitem_tenant_id_idx'), 'cmdb_configurationitem', ['tenant_id'], unique=False)
    op.create_table('core_auditlog',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=80), nullable=False),
    sa.Column('object_id', sa.String(length=64), nullable=False),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['core_user.id'], name=op.f('core_auditlog_actor_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_auditlog_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_auditlog_pkey'))
    )
    op.create_index(op.f('core_auditlog_actor_id_idx'), 'core_auditlog', ['actor_id'], unique=False)
    op.create_index(op.f('core_auditlog_created_at_idx'), 'core_auditlog', ['created_at'], unique=False)
    op.create_index(op.f('core_auditlog_tenant_id_idx'), 'core_auditlog', ['tenant_id'], unique=False)
    op.create_table('core_contactidentity',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('core_contactidentity_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('core_contactidentity_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_contactidentity_pkey')),
    sa.UniqueConstraint('tenant_id', 'kind', 'value', name='core_contactidentity_tenant_id_kind_value_uniq')
    )
    op.create_index(op.f('core_contactidentity_tenant_id_idx'), 'core_contactidentity', ['tenant_id'], unique=False)
    op.create_index(op.f('core_contactidentity_user_id_idx'), 'core_contactidentity', ['user_id'], unique=False)
    op.create_table('core_team_members',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('team_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['core_team.id'], name=op.f('core_team_members_team_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('core_team_members_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('core_team_members_pkey')),
    sa.UniqueConstraint('team_id', 'user_id', name='core_team_members_team_id_user_id_uniq')
    )
    op.create_index(op.f('core_team_members_team_id_idx'), 'core_team_members', ['team_id'], unique=False)
    op.create_index(op.f('core_team_members_user_id_idx'), 'core_team_members', ['user_id'], unique=False)
    op.create_table('tickets_cannedresponse',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('ticket_type_id', sa.BigInteger(), nullable=True),
    sa.Column('owner_id', sa.BigInteger(), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['core_user.id'], name=op.f('tickets_cannedresponse_owner_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_cannedresponse_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_type_id'], ['workflow_tickettype.id'], name=op.f('tickets_cannedresponse_ticket_type_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_cannedresponse_pkey'))
    )
    op.create_index(op.f('tickets_cannedresponse_owner_id_idx'), 'tickets_cannedresponse', ['owner_id'], unique=False)
    op.create_index(op.f('tickets_cannedresponse_tenant_id_idx'), 'tickets_cannedresponse', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_cannedresponse_ticket_type_id_idx'), 'tickets_cannedresponse', ['ticket_type_id'], unique=False)
    op.create_table('tickets_notificationpreference',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('email_enabled', sa.Boolean(), nullable=False),
    sa.Column('in_app_enabled', sa.Boolean(), nullable=False),
    sa.Column('notify_on_assignment', sa.Boolean(), nullable=False),
    sa.Column('notify_on_replies', sa.Boolean(), nullable=False),
    sa.Column('notify_on_approvals', sa.Boolean(), nullable=False),
    sa.Column('notify_on_sla', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_notificationpreference_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('tickets_notificationpreference_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_notificationpreference_pkey')),
    sa.UniqueConstraint('user_id', name=op.f('tickets_notificationpreference_user_id_uniq'))
    )
    op.create_index(op.f('tickets_notificationpreference_tenant_id_idx'), 'tickets_notificationpreference', ['tenant_id'], unique=False)
    op.create_table('tickets_savedqueue',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('is_shared', sa.Boolean(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_savedqueue_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('tickets_savedqueue_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_savedqueue_pkey')),
    sa.UniqueConstraint('tenant_id', 'user_id', 'name', name='tickets_savedqueue_tenant_id_user_id_name_uniq')
    )
    op.create_index(op.f('tickets_savedqueue_tenant_id_idx'), 'tickets_savedqueue', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_savedqueue_user_id_idx'), 'tickets_savedqueue', ['user_id'], unique=False)
    op.create_table('cmdb_configurationitem_depends_on',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('from_configurationitem_id', sa.BigInteger(), nullable=False),
    sa.Column('to_configurationitem_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['from_configurationitem_id'], ['cmdb_configurationitem.id'], name=op.f('cmdb_configurationitem_depends_on_from_configurationitem_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_configurationitem_id'], ['cmdb_configurationitem.id'], name=op.f('cmdb_configurationitem_depends_on_to_configurationitem_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('cmdb_configurationitem_depends_on_pkey')),
    sa.UniqueConstraint('from_configurationitem_id', 'to_configurationitem_id', name='cmdb_configurationitem_depends_on_uniq')
    )
    op.create_index(op.f('cmdb_configurationitem_depends_on_from_configurationitem_id_idx'), 'cmdb_configurationitem_depends_on', ['from_configurationitem_id'], unique=False)
    op.create_index(op.f('cmdb_configurationitem_depends_on_to_configurationitem_id_idx'), 'cmdb_configurationitem_depends_on', ['to_configurationitem_id'], unique=False)
    op.create_table('tickets_ticket',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('ticket_type_id', sa.BigInteger(), nullable=False),
    sa.Column('state_id', sa.BigInteger(), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('requester_id', sa.BigInteger(), nullable=True),
    sa.Column('created_by_id', sa.BigInteger(), nullable=True),
    sa.Column('assignee_id', sa.BigInteger(), nullable=True),
    sa.Column('team_id', sa.BigInteger(), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('service_id', sa.BigInteger(), nullable=True),
    sa.Column('ci_id', sa.BigInteger(), nullable=True),
    sa.Column('impact', sa.SmallInteger(), nullable=False),
    sa.Column('urgency', sa.SmallInteger(), nullable=False),
    sa.Column('priority_id', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=True),
    sa.Column('tags', sa.ARRAY(sa.String(length=50)), nullable=False),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_major_incident', sa.Boolean(), nullable=False),
    sa.Column('incident_commander_id', sa.BigInteger(), nullable=True),
    sa.Column('major_incident_status', sa.String(length=30), nullable=False),
    sa.Column('post_incident_review', sa.Text(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('sla_policy_id', sa.BigInteger(), nullable=True),
    sa.Column('sla_status', sa.String(length=12), nullable=False),
    sa.Column('response_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sla_paused_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('response_breached', sa.Boolean(), nullable=False),
    sa.Column('resolution_breached', sa.Boolean(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_code', sa.String(length=60), nullable=False),
    sa.Column('resolution_notes', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('ai_summary', sa.Text(), nullable=False),
    sa.Column('ai_triage', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('tickets_ticket_account_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['assignee_id'], ['core_user.id'], name=op.f('tickets_ticket_assignee_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['workflow_category.id'], name=op.f('tickets_ticket_category_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels_channel.id'], name=op.f('tickets_ticket_channel_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ci_id'], ['cmdb_configurationitem.id'], name=op.f('tickets_ticket_ci_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['core_user.id'], name=op.f('tickets_ticket_created_by_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_commander_id'], ['core_user.id'], name=op.f('tickets_ticket_incident_commander_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['tickets_ticket.id'], name=op.f('tickets_ticket_parent_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['priority_id'], ['workflow_priority.id'], name=op.f('tickets_ticket_priority_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requester_id'], ['core_user.id'], name=op.f('tickets_ticket_requester_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['service_id'], ['cmdb_service.id'], name=op.f('tickets_ticket_service_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sla_policy_id'], ['workflow_slapolicy.id'], name=op.f('tickets_ticket_sla_policy_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['state_id'], ['workflow_state.id'], name=op.f('tickets_ticket_state_id_fk'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['team_id'], ['core_team.id'], name=op.f('tickets_ticket_team_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_ticket_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_type_id'], ['workflow_tickettype.id'], name=op.f('tickets_ticket_ticket_type_id_fk'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_ticket_pkey')),
    sa.UniqueConstraint('tenant_id', 'number', name='tickets_ticket_tenant_id_number_uniq')
    )
    op.create_index('ticket_embedding_hnsw', 'tickets_ticket', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ticket_fts_idx', 'tickets_ticket', [sa.literal_column("to_tsvector('english'::regconfig, COALESCE(subject, '') || ' ' || COALESCE(description, ''))")], unique=False, postgresql_using='gin')
    op.create_index(op.f('tickets_ticket_category_id_idx'), 'tickets_ticket', ['category_id'], unique=False)
    op.create_index(op.f('tickets_ticket_channel_id_idx'), 'tickets_ticket', ['channel_id'], unique=False)
    op.create_index(op.f('tickets_ticket_ci_id_idx'), 'tickets_ticket', ['ci_id'], unique=False)
    op.create_index(op.f('tickets_ticket_created_at_idx'), 'tickets_ticket', ['created_at'], unique=False)
    op.create_index(op.f('tickets_ticket_created_by_id_idx'), 'tickets_ticket', ['created_by_id'], unique=False)
    op.create_index(op.f('tickets_ticket_incident_commander_id_idx'), 'tickets_ticket', ['incident_commander_id'], unique=False)
    op.create_index(op.f('tickets_ticket_parent_id_idx'), 'tickets_ticket', ['parent_id'], unique=False)
    op.create_index(op.f('tickets_ticket_priority_id_idx'), 'tickets_ticket', ['priority_id'], unique=False)
    op.create_index(op.f('tickets_ticket_requester_id_idx'), 'tickets_ticket', ['requester_id'], unique=False)
    op.create_index(op.f('tickets_ticket_service_id_idx'), 'tickets_ticket', ['service_id'], unique=False)
    op.create_index(op.f('tickets_ticket_sla_policy_id_idx'), 'tickets_ticket', ['sla_policy_id'], unique=False)
    op.create_index(op.f('tickets_ticket_state_id_idx'), 'tickets_ticket', ['state_id'], unique=False)
    op.create_index(op.f('tickets_ticket_team_id_idx'), 'tickets_ticket', ['team_id'], unique=False)
    op.create_index('tickets_ticket_tenant_id_account_id_idx', 'tickets_ticket', ['tenant_id', 'account_id'], unique=False)
    op.create_index('tickets_ticket_tenant_id_assignee_id_idx', 'tickets_ticket', ['tenant_id', 'assignee_id'], unique=False)
    op.create_index(op.f('tickets_ticket_tenant_id_idx'), 'tickets_ticket', ['tenant_id'], unique=False)
    op.create_index('tickets_ticket_tenant_id_sla_status_idx', 'tickets_ticket', ['tenant_id', 'sla_status'], unique=False)
    op.create_index('tickets_ticket_tenant_id_state_id_idx', 'tickets_ticket', ['tenant_id', 'state_id'], unique=False)
    op.create_index(op.f('tickets_ticket_ticket_type_id_idx'), 'tickets_ticket', ['ticket_type_id'], unique=False)
    op.create_table('ai_aifeedback',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=True),
    sa.Column('feature', sa.String(length=20), nullable=False),
    sa.Column('suggestion', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('accepted', sa.Boolean(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('ai_aifeedback_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('ai_aifeedback_ticket_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('ai_aifeedback_user_id_fk'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('ai_aifeedback_pkey'))
    )
    op.create_index(op.f('ai_aifeedback_tenant_id_idx'), 'ai_aifeedback', ['tenant_id'], unique=False)
    op.create_index(op.f('ai_aifeedback_ticket_id_idx'), 'ai_aifeedback', ['ticket_id'], unique=False)
    op.create_index(op.f('ai_aifeedback_user_id_idx'), 'ai_aifeedback', ['user_id'], unique=False)
    op.create_table('channels_inboundmessage',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('envelope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels_channel.id'], name=op.f('channels_inboundmessage_channel_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('channels_inboundmessage_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('channels_inboundmessage_ticket_id_fk'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('channels_inboundmessage_pkey')),
    sa.UniqueConstraint('channel_id', 'external_id', name='channels_inboundmessage_channel_id_external_id_uniq')
    )
    op.create_index('channels_inbound_retry_idx', 'channels_inboundmessage', ['status', 'next_attempt_at'], unique=False)
    op.create_index(op.f('channels_inboundmessage_channel_id_idx'), 'channels_inboundmessage', ['channel_id'], unique=False)
    op.create_index(op.f('channels_inboundmessage_received_at_idx'), 'channels_inboundmessage', ['received_at'], unique=False)
    op.create_index(op.f('channels_inboundmessage_tenant_id_idx'), 'channels_inboundmessage', ['tenant_id'], unique=False)
    op.create_index(op.f('channels_inboundmessage_ticket_id_idx'), 'channels_inboundmessage', ['ticket_id'], unique=False)
    op.create_table('kb_knowledgearticle',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('account_id', sa.BigInteger(), nullable=True),
    sa.Column('visibility', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('tags', sa.ARRAY(sa.String(length=50)), nullable=False),
    sa.Column('source_ticket_id', sa.BigInteger(), nullable=True),
    sa.Column('author_id', sa.BigInteger(), nullable=True),
    sa.Column('views', sa.Integer(), nullable=False),
    sa.Column('helpful_votes', sa.Integer(), nullable=False),
    sa.Column('unhelpful_votes', sa.Integer(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['core_account.id'], name=op.f('kb_knowledgearticle_account_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['author_id'], ['core_user.id'], name=op.f('kb_knowledgearticle_author_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['workflow_category.id'], name=op.f('kb_knowledgearticle_category_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_ticket_id'], ['tickets_ticket.id'], name=op.f('kb_knowledgearticle_source_ticket_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('kb_knowledgearticle_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('kb_knowledgearticle_pkey'))
    )
    op.create_index('kb_embedding_hnsw', 'kb_knowledgearticle', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('kb_knowledgearticle_account_id_idx'), 'kb_knowledgearticle', ['account_id'], unique=False)
    op.create_index(op.f('kb_knowledgearticle_author_id_idx'), 'kb_knowledgearticle', ['author_id'], unique=False)
    op.create_index(op.f('kb_knowledgearticle_category_id_idx'), 'kb_knowledgearticle', ['category_id'], unique=False)
    op.create_index(op.f('kb_knowledgearticle_source_ticket_id_idx'), 'kb_knowledgearticle', ['source_ticket_id'], unique=False)
    op.create_index(op.f('kb_knowledgearticle_tenant_id_idx'), 'kb_knowledgearticle', ['tenant_id'], unique=False)
    op.create_table('tickets_approval',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('approver_id', sa.BigInteger(), nullable=False),
    sa.Column('requested_by_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['approver_id'], ['core_user.id'], name=op.f('tickets_approval_approver_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requested_by_id'], ['core_user.id'], name=op.f('tickets_approval_requested_by_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_approval_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_approval_ticket_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_approval_pkey'))
    )
    op.create_index(op.f('tickets_approval_approver_id_idx'), 'tickets_approval', ['approver_id'], unique=False)
    op.create_index(op.f('tickets_approval_requested_by_id_idx'), 'tickets_approval', ['requested_by_id'], unique=False)
    op.create_index(op.f('tickets_approval_tenant_id_idx'), 'tickets_approval', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_approval_ticket_id_idx'), 'tickets_approval', ['ticket_id'], unique=False)
    op.create_table('tickets_csatsurvey',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('token', sa.String(length=48), nullable=False),
    sa.Column('score', sa.SmallInteger(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_csatsurvey_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_csatsurvey_ticket_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_csatsurvey_pkey')),
    sa.UniqueConstraint('ticket_id', name=op.f('tickets_csatsurvey_ticket_id_uniq')),
    sa.UniqueConstraint('token', name=op.f('tickets_csatsurvey_token_uniq'))
    )
    op.create_index(op.f('tickets_csatsurvey_tenant_id_idx'), 'tickets_csatsurvey', ['tenant_id'], unique=False)
    op.create_table('tickets_interaction',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('author_id', sa.BigInteger(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('direction', sa.String(length=10), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=True),
    sa.Column('external_message_id', sa.String(length=255), nullable=False),
    sa.Column('delivery_status', sa.String(length=10), nullable=False),
    sa.Column('delivery_error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['core_user.id'], name=op.f('tickets_interaction_author_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels_channel.id'], name=op.f('tickets_interaction_channel_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_interaction_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_interaction_ticket_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_interaction_pkey'))
    )
    op.create_index(op.f('tickets_interaction_author_id_idx'), 'tickets_interaction', ['author_id'], unique=False)
    op.create_index(op.f('tickets_interaction_channel_id_idx'), 'tickets_interaction', ['channel_id'], unique=False)
    op.create_index(op.f('tickets_interaction_created_at_idx'), 'tickets_interaction', ['created_at'], unique=False)
    op.create_index(op.f('tickets_interaction_external_message_id_idx'), 'tickets_interaction', ['external_message_id'], unique=False)
    op.create_index(op.f('tickets_interaction_tenant_id_idx'), 'tickets_interaction', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_interaction_ticket_id_idx'), 'tickets_interaction', ['ticket_id'], unique=False)
    op.create_table('tickets_notification',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('recipient_id', sa.BigInteger(), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.String(length=500), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['core_user.id'], name=op.f('tickets_notification_actor_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['recipient_id'], ['core_user.id'], name=op.f('tickets_notification_recipient_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_notification_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_notification_ticket_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_notification_pkey'))
    )
    op.create_index('tickets_not_tenant__74c207_idx', 'tickets_notification', ['tenant_id', 'recipient_id', 'is_read'], unique=False)
    op.create_index(op.f('tickets_notification_actor_id_idx'), 'tickets_notification', ['actor_id'], unique=False)
    op.create_index(op.f('tickets_notification_created_at_idx'), 'tickets_notification', ['created_at'], unique=False)
    op.create_index(op.f('tickets_notification_is_read_idx'), 'tickets_notification', ['is_read'], unique=False)
    op.create_index(op.f('tickets_notification_recipient_id_idx'), 'tickets_notification', ['recipient_id'], unique=False)
    op.create_index(op.f('tickets_notification_tenant_id_idx'), 'tickets_notification', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_notification_ticket_id_idx'), 'tickets_notification', ['ticket_id'], unique=False)
    op.create_table('tickets_ticket_watchers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_ticket_watchers_ticket_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('tickets_ticket_watchers_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_ticket_watchers_pkey')),
    sa.UniqueConstraint('ticket_id', 'user_id', name='tickets_ticket_watchers_ticket_id_user_id_uniq')
    )
    op.create_index(op.f('tickets_ticket_watchers_ticket_id_idx'), 'tickets_ticket_watchers', ['ticket_id'], unique=False)
    op.create_index(op.f('tickets_ticket_watchers_user_id_idx'), 'tickets_ticket_watchers', ['user_id'], unique=False)
    op.create_table('tickets_ticketevent',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['core_user.id'], name=op.f('tickets_ticketevent_actor_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_ticketevent_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_ticketevent_ticket_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_ticketevent_pkey'))
    )
    op.create_index(op.f('tickets_ticketevent_actor_id_idx'), 'tickets_ticketevent', ['actor_id'], unique=False)
    op.create_index(op.f('tickets_ticketevent_created_at_idx'), 'tickets_ticketevent', ['created_at'], unique=False)
    op.create_index(op.f('tickets_ticketevent_tenant_id_idx'), 'tickets_ticketevent', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_ticketevent_ticket_id_idx'), 'tickets_ticketevent', ['ticket_id'], unique=False)
    op.create_table('tickets_ticketlink',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=False),
    sa.Column('target_id', sa.BigInteger(), nullable=False),
    sa.Column('link_type', sa.String(length=20), nullable=False),
    sa.Column('created_by_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['core_user.id'], name=op.f('tickets_ticketlink_created_by_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['tickets_ticket.id'], name=op.f('tickets_ticketlink_source_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_id'], ['tickets_ticket.id'], name=op.f('tickets_ticketlink_target_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_ticketlink_tenant_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_ticketlink_pkey')),
    sa.UniqueConstraint('source_id', 'target_id', 'link_type', name='tickets_ticketlink_source_id_target_id_link_type_uniq')
    )
    op.create_index(op.f('tickets_ticketlink_created_by_id_idx'), 'tickets_ticketlink', ['created_by_id'], unique=False)
    op.create_index(op.f('tickets_ticketlink_source_id_idx'), 'tickets_ticketlink', ['source_id'], unique=False)
    op.create_index(op.f('tickets_ticketlink_target_id_idx'), 'tickets_ticketlink', ['target_id'], unique=False)
    op.create_index(op.f('tickets_ticketlink_tenant_id_idx'), 'tickets_ticketlink', ['tenant_id'], unique=False)
    op.create_table('tickets_timeentry',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('minutes', sa.Integer(), nullable=False),
    sa.Column('billable', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_timeentry_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_timeentry_ticket_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core_user.id'], name=op.f('tickets_timeentry_user_id_fk'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_timeentry_pkey'))
    )
    op.create_index(op.f('tickets_timeentry_tenant_id_idx'), 'tickets_timeentry', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_timeentry_ticket_id_idx'), 'tickets_timeentry', ['ticket_id'], unique=False)
    op.create_index(op.f('tickets_timeentry_user_id_idx'), 'tickets_timeentry', ['user_id'], unique=False)
    op.create_table('tickets_attachment',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('interaction_id', sa.BigInteger(), nullable=True),
    sa.Column('file', sa.String(length=500), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('scan_status', sa.String(length=10), nullable=False),
    sa.Column('uploaded_by_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['interaction_id'], ['tickets_interaction.id'], name=op.f('tickets_attachment_interaction_id_fk'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['core_tenant.id'], name=op.f('tickets_attachment_tenant_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets_ticket.id'], name=op.f('tickets_attachment_ticket_id_fk'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploaded_by_id'], ['core_user.id'], name=op.f('tickets_attachment_uploaded_by_id_fk'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('tickets_attachment_pkey'))
    )
    op.create_index(op.f('tickets_attachment_interaction_id_idx'), 'tickets_attachment', ['interaction_id'], unique=False)
    op.create_index(op.f('tickets_attachment_tenant_id_idx'), 'tickets_attachment', ['tenant_id'], unique=False)
    op.create_index(op.f('tickets_attachment_ticket_id_idx'), 'tickets_attachment', ['ticket_id'], unique=False)
    op.create_index(op.f('tickets_attachment_uploaded_by_id_idx'), 'tickets_attachment', ['uploaded_by_id'], unique=False)
    # ### end Alembic commands ###

    op.create_index(op.f('tickets_ticket_account_id_idx'), 'tickets_ticket', ['account_id'], unique=False)
    op.create_index(op.f('tickets_ticket_assignee_id_idx'), 'tickets_ticket', ['assignee_id'], unique=False)
    op.create_index(op.f('workflow_state_key_idx'), 'workflow_state', ['key'], unique=False)
    op.create_index(op.f('workflow_tickettype_key_idx'), 'workflow_tickettype', ['key'], unique=False)
    op.create_index(op.f('workflow_customfield_key_idx'), 'workflow_customfield', ['key'], unique=False)
    op.create_foreign_key('core_department_head_id_fk', 'core_department', 'core_user', ['head_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('workflow_workflow_customer_reply_state_id_fk', 'workflow_workflow', 'workflow_state', ['customer_reply_state_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    bind = op.get_bind()
    for fn in ("public.resolve_csat_survey_by_token(text)", "public.resolve_channel_by_inbound_token(text)",
               "public.auth_lookup_user_by_email(text)", "public.get_verified_tenant_id()"):
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {fn}")
    bind.exec_driver_sql("DROP SCHEMA IF EXISTS opsdesk_secure CASCADE")
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS opsdesk_audit_immutable() CASCADE")
    op.drop_constraint('core_department_head_id_fk', 'core_department', type_='foreignkey')
    op.drop_constraint('workflow_workflow_customer_reply_state_id_fk', 'workflow_workflow', type_='foreignkey')
    op.drop_table('tickets_attachment')
    op.drop_table('tickets_timeentry')
    op.drop_table('tickets_ticketlink')
    op.drop_table('tickets_ticketevent')
    op.drop_table('tickets_ticket_watchers')
    op.drop_table('tickets_notification')
    op.drop_table('tickets_interaction')
    op.drop_table('tickets_csatsurvey')
    op.drop_table('tickets_approval')
    op.drop_table('kb_knowledgearticle')
    op.drop_table('channels_inboundmessage')
    op.drop_table('ai_aifeedback')
    op.drop_table('tickets_ticket')
    op.drop_table('cmdb_configurationitem_depends_on')
    op.drop_table('tickets_savedqueue')
    op.drop_table('tickets_notificationpreference')
    op.drop_table('tickets_cannedresponse')
    op.drop_table('core_team_members')
    op.drop_table('core_contactidentity')
    op.drop_table('core_auditlog')
    op.drop_table('cmdb_configurationitem')
    op.drop_table('tickets_servicecatalogitem')
    op.drop_table('core_user')
    op.drop_table('core_team_accounts')
    op.drop_table('cmdb_service')
    op.drop_table('workflow_transition')
    op.drop_table('workflow_slatarget')
    op.drop_table('workflow_slapolicy_ticket_types')
    op.drop_table('workflow_customfield')
    op.drop_table('workflow_category')
    op.drop_table('core_account')
    op.drop_table('workflow_tickettype')
    op.drop_table('workflow_state')
    op.drop_table('workflow_slapolicy')
    op.drop_table('workflow_prioritymatrix')
    op.drop_table('workflow_workflow')
    op.drop_table('workflow_priority')
    op.drop_table('tickets_ticketsequence')
    op.drop_table('core_team')
    op.drop_table('core_department')
    op.drop_table('core_businesshours')
    op.drop_table('channels_channel')
    op.drop_table('automation_rule')
    op.drop_table('core_tenant')
