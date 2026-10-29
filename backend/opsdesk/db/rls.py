"""Row-level security, SECURITY DEFINER functions, the signed tenant-context verifier and its keystore.

Everything is idempotent;
``apply()`` can be re-run after adding tenant-owned tables.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..settings import settings
from .base import Base

COND_BARE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint"
COND_SIGNED = "tenant_id = public.get_verified_tenant_id()"

# Tables without tenant_id, isolated through their parent row.
CHILD_TABLES = {
    "workflow_state": ("workflow_workflow", "workflow_id"),
    "workflow_transition": ("workflow_workflow", "workflow_id"),
    "workflow_slatarget": ("workflow_slapolicy", "policy_id"),
}
# Many-to-many tables: both sides must be visible through their own (already RLS-protected) tables.
M2M_TABLES = {
    "tickets_ticket_watchers": (
        "EXISTS (SELECT 1 FROM public.tickets_ticket t WHERE t.id = ticket_id) AND "
        "EXISTS (SELECT 1 FROM public.core_user u WHERE u.id = user_id)"
    ),
    "core_team_members": (
        "EXISTS (SELECT 1 FROM public.core_team t WHERE t.id = team_id) AND "
        "EXISTS (SELECT 1 FROM public.core_user u WHERE u.id = user_id)"
    ),
    "core_team_accounts": (
        "EXISTS (SELECT 1 FROM public.core_team t WHERE t.id = team_id) AND "
        "EXISTS (SELECT 1 FROM public.core_account a WHERE a.id = account_id)"
    ),
    "cmdb_configurationitem_depends_on": (
        "EXISTS (SELECT 1 FROM public.cmdb_configurationitem c WHERE c.id = from_configurationitem_id) AND "
        "EXISTS (SELECT 1 FROM public.cmdb_configurationitem c WHERE c.id = to_configurationitem_id)"
    ),
    "workflow_slapolicy_ticket_types": (
        "EXISTS (SELECT 1 FROM public.workflow_slapolicy p WHERE p.id = slapolicy_id) AND "
        "EXISTS (SELECT 1 FROM public.workflow_tickettype tt WHERE tt.id = tickettype_id)"
    ),
}
# RLS enabled but not FORCEd: login/webhook/CSAT resolution happen before a tenant is known and run
# through SECURITY DEFINER functions owned by the schema owner.
NO_FORCE_RLS = {"core_user", "channels_channel", "tickets_csatsurvey"}

_ROLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def tenant_tables() -> list[str]:
    """Every mapped table that carries a ``tenant_id`` column (the model-driven policy set)."""
    return sorted(t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c)


def role_name(role: str | None = None) -> str:
    r = role or settings.db_app_role
    if not _ROLE_RE.match(r):
        raise ValueError(f"Invalid database application role name: {r}")
    return r


def policy_statements(cond: str = COND_SIGNED) -> list[str]:
    sql: list[str] = []
    for t in tenant_tables():
        sql.append(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        sql.append(f"ALTER TABLE {t} {'NO FORCE' if t in NO_FORCE_RLS else 'FORCE'} ROW LEVEL SECURITY")
        sql += [
            f"DROP POLICY IF EXISTS tenant_isolation ON {t}",
            f"CREATE POLICY tenant_isolation ON {t} USING ({cond}) WITH CHECK ({cond})",
        ]
    for t, (parent, fk) in CHILD_TABLES.items():
        c = f"EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {fk})"
        sql += [
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {t}",
            f"CREATE POLICY tenant_isolation ON {t} USING ({c}) WITH CHECK ({c})",
        ]
    for t, c in M2M_TABLES.items():
        sql += [
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {t}",
            f"CREATE POLICY tenant_isolation ON {t} USING ({c}) WITH CHECK ({c})",
        ]
    return sql


AUDIT_TRIGGER_SQL = [
    """CREATE OR REPLACE FUNCTION opsdesk_audit_immutable() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit log is append-only'; END $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS audit_immutable ON core_auditlog",
    "CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON core_auditlog "
    "FOR EACH ROW EXECUTE FUNCTION opsdesk_audit_immutable()",
]


def grant_statements(role: str | None = None) -> list[str]:
    r = role_name(role)
    return [
        f'GRANT USAGE ON SCHEMA public TO "{r}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{r}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{r}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{r}"',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "{r}"',
    ]


# --------------------------------------------------------------------------- SECURITY DEFINER lookups
def lookup_functions_sql(role: str | None = None) -> str:
    r = role_name(role)
    return f"""
CREATE OR REPLACE FUNCTION public.auth_lookup_user_by_email(p_email text)
RETURNS TABLE (user_id bigint, email varchar, password_hash varchar, is_active boolean, tenant_id bigint,
               tenant_is_active boolean, mfa_enabled boolean, role varchar)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE v_count integer;
BEGIN
    IF p_email IS NULL OR trim(p_email) = '' THEN RETURN; END IF;
    SELECT count(*) INTO v_count FROM public.core_user u WHERE lower(u.email) = lower(trim(p_email));
    IF v_count > 1 THEN
        RAISE WARNING 'Ambiguous identity: % accounts found matching email %', v_count, p_email;
        RETURN;
    END IF;
    RETURN QUERY
    SELECT u.id AS user_id, u.email, u.password AS password_hash, u.is_active, u.tenant_id,
           COALESCE(t.is_active, false) AS tenant_is_active, u.mfa_enabled, u.role
    FROM public.core_user u LEFT JOIN public.core_tenant t ON t.id = u.tenant_id
    WHERE lower(u.email) = lower(trim(p_email));
END;
$$;

CREATE OR REPLACE FUNCTION public.resolve_channel_by_inbound_token(p_token text)
RETURNS TABLE (channel_id bigint, tenant_id bigint, kind varchar, is_active boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_token IS NULL OR trim(p_token) = '' THEN RETURN; END IF;
    RETURN QUERY
    SELECT c.id AS channel_id, c.tenant_id, c.kind, c.is_active
    FROM public.channels_channel c INNER JOIN public.core_tenant t ON t.id = c.tenant_id
    WHERE c.inbound_token = trim(p_token) AND c.is_active = true AND t.is_active = true;
END;
$$;

DROP FUNCTION IF EXISTS public.resolve_csat_survey_by_token(text);
CREATE FUNCTION public.resolve_csat_survey_by_token(p_token text)
RETURNS TABLE (survey_id bigint, tenant_id bigint, ticket_id bigint, score smallint, responded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF p_token IS NULL OR btrim(p_token) = '' THEN RETURN; END IF;
    RETURN QUERY
    SELECT s.id AS survey_id, s.tenant_id, s.ticket_id, s.score, s.responded_at
    FROM public.tickets_csatsurvey s JOIN public.core_tenant t ON t.id = s.tenant_id
    WHERE s.token = btrim(p_token) AND t.is_active = true;
END;
$$;

ALTER FUNCTION public.resolve_csat_survey_by_token(text) OWNER TO opsdesk_owner;
REVOKE ALL ON FUNCTION public.auth_lookup_user_by_email(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.resolve_channel_by_inbound_token(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.resolve_csat_survey_by_token(text) FROM PUBLIC;

DO $$
DECLARE v_role text := '{r}';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
        RAISE EXCEPTION 'Required runtime role "%" does not exist in pg_roles. Ensure backend/sql/00_init_roles.sql has been executed.', v_role;
    END IF;
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.auth_lookup_user_by_email(text) TO %I', v_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.resolve_channel_by_inbound_token(text) TO %I', v_role);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.resolve_csat_survey_by_token(text) TO %I', v_role);
END $$;
"""


# --------------------------------------------------------------------------- signed tenant context
def signed_context_sql(role: str | None = None) -> str:
    """Keystore schema, immutability trigger, dev key seed and ``public.get_verified_tenant_id()``."""
    r = role_name(role)
    return f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

CREATE SCHEMA IF NOT EXISTS opsdesk_secure;
ALTER SCHEMA opsdesk_secure OWNER TO opsdesk_owner;
REVOKE ALL ON SCHEMA opsdesk_secure FROM PUBLIC;

CREATE TABLE IF NOT EXISTS opsdesk_secure.signing_keys (
    key_id varchar(32) PRIMARY KEY,
    secret_key bytea NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    retired_at timestamptz NULL,
    revoked_at timestamptz NULL,
    CONSTRAINT chk_status CHECK (status IN ('active', 'retired', 'revoked')),
    CONSTRAINT chk_secret_key_len CHECK (octet_length(secret_key) = 32),
    CONSTRAINT chk_key_id_fmt CHECK (key_id ~ '^[a-zA-Z0-9_-]{{1,32}}$')
);
ALTER TABLE opsdesk_secure.signing_keys OWNER TO opsdesk_owner;
REVOKE ALL ON TABLE opsdesk_secure.signing_keys FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE opsdesk_secure.signing_keys TO opsdesk_owner;

DO $$
DECLARE v_role text := '{r}';
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
        EXECUTE format('REVOKE ALL ON SCHEMA opsdesk_secure FROM %I', v_role);
        EXECUTE format('REVOKE ALL ON TABLE opsdesk_secure.signing_keys FROM %I', v_role);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION opsdesk_secure.enforce_key_immutability()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Key records are immutable and cannot be deleted'; END IF;
    IF NEW.key_id <> OLD.key_id OR NEW.secret_key <> OLD.secret_key THEN
        RAISE EXCEPTION 'Key ID and secret key material are permanently immutable';
    END IF;
    IF OLD.status = 'retired' AND NEW.status = 'active' THEN
        RAISE EXCEPTION 'Illegal state transition: retired keys cannot return to active';
    END IF;
    IF OLD.status = 'revoked' AND NEW.status <> 'revoked' THEN
        RAISE EXCEPTION 'Illegal state transition: revoked keys cannot change status';
    END IF;
    RETURN NEW;
END;
$$;
ALTER FUNCTION opsdesk_secure.enforce_key_immutability() OWNER TO opsdesk_owner;

DROP TRIGGER IF EXISTS trg_signing_keys_immutable ON opsdesk_secure.signing_keys;
CREATE TRIGGER trg_signing_keys_immutable BEFORE UPDATE OR DELETE ON opsdesk_secure.signing_keys
FOR EACH ROW EXECUTE FUNCTION opsdesk_secure.enforce_key_immutability();

CREATE OR REPLACE FUNCTION public.get_verified_tenant_id()
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_tenant_id_str text; v_timestamp_str text; v_key_id text; v_signature text; v_nonce text;
    v_secret bytea; v_expected_sig text; v_now_epoch bigint; v_req_epoch bigint; v_tenant_id bigint;
    v_diff int := 0; i int; v_expected_b bytea; v_given_b bytea;
BEGIN
    v_tenant_id_str := current_setting('app.tenant_id', true);
    v_timestamp_str := current_setting('app.tenant_timestamp', true);
    v_key_id        := current_setting('app.tenant_key_id', true);
    v_signature     := current_setting('app.tenant_signature', true);
    v_nonce         := current_setting('app.tenant_nonce', true);

    IF v_tenant_id_str IS NULL OR v_timestamp_str IS NULL OR v_key_id IS NULL
       OR v_signature IS NULL OR v_nonce IS NULL THEN
        RETURN NULL;
    END IF;

    IF length(v_tenant_id_str) < 1 OR length(v_tenant_id_str) > 19 OR length(v_timestamp_str) != 10
       OR length(v_key_id) < 1 OR length(v_key_id) > 32 OR length(v_nonce) != 32 OR length(v_signature) != 64 THEN
        RETURN NULL;
    END IF;

    IF v_tenant_id_str !~ '^[1-9][0-9]{{0,18}}$' OR v_timestamp_str !~ '^[1-9][0-9]{{9}}$'
       OR v_key_id !~ '^[a-zA-Z0-9_-]{{1,32}}$' OR v_nonce !~ '^[a-f0-9]{{32}}$'
       OR v_signature !~ '^[a-f0-9]{{64}}$' THEN
        RETURN NULL;
    END IF;

    IF length(v_tenant_id_str) = 19 AND v_tenant_id_str > '9223372036854775807' THEN RETURN NULL; END IF;

    v_tenant_id := v_tenant_id_str::bigint;
    v_req_epoch := v_timestamp_str::bigint;
    v_now_epoch := extract(epoch from pg_catalog.statement_timestamp())::bigint;
    IF (v_req_epoch - v_now_epoch) > 5 THEN RETURN NULL; END IF;
    IF (v_now_epoch - v_req_epoch) > 60 THEN RETURN NULL; END IF;

    SELECT secret_key INTO v_secret FROM opsdesk_secure.signing_keys WHERE key_id = v_key_id AND status = 'active';
    IF v_secret IS NULL THEN RETURN NULL; END IF;

    v_expected_sig := pg_catalog.encode(
        public.hmac(
            pg_catalog.convert_to('v1:' || v_tenant_id_str || ':' || v_timestamp_str || ':' || v_key_id || ':' || v_nonce, 'UTF8'),
            v_secret, 'sha256'),
        'hex');

    v_expected_b := pg_catalog.decode(v_expected_sig, 'hex');
    v_given_b    := pg_catalog.decode(v_signature, 'hex');
    FOR i IN 0..31 LOOP
        v_diff := v_diff | (pg_catalog.get_byte(v_expected_b, i) # pg_catalog.get_byte(v_given_b, i));
    END LOOP;
    IF v_diff != 0 THEN RETURN NULL; END IF;

    RETURN v_tenant_id;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

ALTER FUNCTION public.get_verified_tenant_id() OWNER TO opsdesk_owner;
REVOKE ALL ON FUNCTION public.get_verified_tenant_id() FROM PUBLIC;

DO $$
DECLARE v_role text := '{r}';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
        RAISE EXCEPTION 'Required runtime role "%" does not exist in pg_roles.', v_role;
    END IF;
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.get_verified_tenant_id() TO %I', v_role);
END $$;
"""


def seed_signing_key_sql(key_id: str, secret_hex: str) -> str:
    """Insert (idempotently) a signing key. Used for the dev key on fresh DBs and by ``opsdesk keys add``."""
    if not re.match(r"^[a-zA-Z0-9_-]{1,32}$", key_id) or not re.match(r"^[a-fA-F0-9]{64}$", secret_hex):
        raise ValueError("invalid signing key material")
    return (
        "INSERT INTO opsdesk_secure.signing_keys (key_id, secret_key, status) "
        f"VALUES ('{key_id}', decode('{secret_hex.lower()}', 'hex'), 'active') ON CONFLICT (key_id) DO NOTHING"
    )


def apply(conn: Connection, role: str | None = None, signed: bool = True) -> int:
    """Create/refresh RLS policies, the audit trigger and grants. Returns the number of protected relations."""
    has_verifier = conn.execute(text(
        "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = 'get_verified_tenant_id'")).first()
    cond = COND_SIGNED if (signed and has_verifier) else COND_BARE
    for stmt in policy_statements(cond):
        conn.exec_driver_sql(stmt)
    for stmt in AUDIT_TRIGGER_SQL:
        conn.exec_driver_sql(stmt)
    for stmt in grant_statements(role):
        conn.exec_driver_sql(stmt)
    return len(tenant_tables()) + len(CHILD_TABLES) + len(M2M_TABLES)


def run_script(conn: Connection, script: str) -> None:
    """Run a multi-statement script verbatim. SQLAlchemy would hand ``%`` to psycopg as a placeholder
    marker (breaking ``format('%I')`` in PL/pgSQL), so execute on the DBAPI connection with no parameters,
    inside the caller's transaction."""
    conn.connection.driver_connection.execute(script)


def install_functions(conn: Connection, role: str | None = None) -> None:
    run_script(conn, signed_context_sql(role))
    run_script(conn, lookup_functions_sql(role))
