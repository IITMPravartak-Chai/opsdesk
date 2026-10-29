-- Run ONCE per database as an administrator (RDS master user / Azure admin / docker superuser).
-- Two roles: the schema OWNER (runs migrations, bypasses RLS) and the APP role
-- (used by api/worker/beat, subject to row-level security).
-- Replace the passwords, or better, manage them in your secrets manager.

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'opsdesk_owner') THEN
    CREATE ROLE opsdesk_owner LOGIN PASSWORD 'CHANGE_ME_owner';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'opsdesk_app') THEN
    CREATE ROLE opsdesk_app LOGIN PASSWORD 'CHANGE_ME_app';
  END IF;
END $$;

-- Extensions need elevated rights on managed Postgres, so create them here.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;  -- signed tenant keystore (core.0006)

GRANT CONNECT ON DATABASE opsdesk TO opsdesk_owner, opsdesk_app;
-- core.0006 (run by opsdesk_owner) executes CREATE SCHEMA IF NOT EXISTS opsdesk_secure. PostgreSQL
-- checks the database CREATE privilege before the IF NOT EXISTS test, so the owner needs it even
-- if the schema already exists. It permits creating schemas (and trusted extensions) only: no
-- superuser, CREATEDB, CREATEROLE or BYPASSRLS, and nothing extra for opsdesk_app.
GRANT CREATE ON DATABASE opsdesk TO opsdesk_owner;
GRANT ALL ON SCHEMA public TO opsdesk_owner;
ALTER SCHEMA public OWNER TO opsdesk_owner;
GRANT USAGE ON SCHEMA public TO opsdesk_app;
-- Table/sequence grants for opsdesk_app are applied by `entrypoint.sh migrate`
-- (opsdesk apply-rls with DB_APP_ROLE=opsdesk_app), including default privileges.
