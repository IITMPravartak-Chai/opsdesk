# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OpsDesk: multi-tenant ITIL service desk. **FastAPI + SQLAlchemy 2 / Alembic + Celery** on PostgreSQL 16 (pgvector, pg_trgm) and Redis, with a React 18 PWA (Vite). `README.md` is the product/ops guide and `DEPLOYMENT.md` the AWS runbook.

## Commands

Backend (run from `backend/`; needs PostgreSQL with pgvector and Redis, see README "Local development without Docker"):

```bash
export PYTHONPATH=$PWD OPSDESK_DEBUG=1 USE_LOCAL_CACHE=1 CELERY_TASK_ALWAYS_EAGER=1 DB_APP_ROLE=opsdesk_app
python -m pytest                                  # whole suite (~520 tests); builds a throwaway DB through Alembic
python -m pytest tests/test_api_tickets.py -k name  # single test
python -m opsdesk migrate                         # alembic upgrade head + RLS policies/grants (owner credentials)
python -m opsdesk --help                          # create-tenant, create-user, reset-password, seed-demo, keys, tenants, check ...
uvicorn opsdesk.api.main:app --port 8001 --reload
celery -A opsdesk.worker.celery_app worker       # and `beat` (exactly one instance)
alembic -c alembic.ini check                      # model/migration drift check
```

Test infrastructure is configured by env vars read in `tests/conftest.py` (`OPSDESK_TEST_ADMIN_URL` superuser URL, `OPSDESK_TEST_REDIS_URL`); `OPSDESK_PBKDF2_ITERATIONS` lowers password work factor for tests only.

Frontend (from the repo root): `npm run dev` (port 5173, proxies `/api` to :8001), `npm run build`, `npm test` (node's built-in runner on `src/**/*.test.js`; tests cover pure helper modules only).

Full stack: `cp .env.example .env`, fill `JWT_SECRET_KEY`, `FIELD_ENCRYPTION_KEYS`, `OPSDESK_SIGNING_KEY_ID/SECRET`, then `docker compose up -d --build`; `docker compose run --rm api seed-demo` for demo data. UI on :8080, Mailpit on :8025. Never read or print `.env` (it holds credentials); use `.env.example`.

## Architecture

**One API process family.** nginx (`nginx.conf`) sends only `/api` and `/healthz` to the FastAPI app (`backend/opsdesk/api/main.py`); SSE is unbuffered. Routers live in `opsdesk/api/routers/`. Ticket reads use raw async psycopg repositories (`api/repositories/`); everything else runs sync SQLAlchemy sessions in a threadpool via `api/runtime.run_in_tenant`. All tenant configuration is an API route plus a React page (schema-driven console: `api/config_resources.py` + `src/pages/Configuration.jsx`).

**Tenant isolation is enforced twice.** App code filters by tenant, and PostgreSQL RLS enforces it. The app connects as non-owner role `opsdesk_app`; migrations run as `opsdesk_owner` (`MIGRATION_DATABASE_URL`). A request's tenant is set through a *signed* context (`opsdesk/tenancy.py`, `public.get_verified_tenant_id`, keys in `opsdesk_secure.signing_keys`, replay protection in Redis); sessions come from `db/session.py` (`tenant_session`, `plain_session`). Users, tickets etc. are invisible to `plain_session`: pre-auth lookups (`auth_lookup_user_by_email`, `resolve_channel_by_inbound_token`, `resolve_csat_survey_by_token`) are SECURITY DEFINER functions, all RLS/audit-trigger SQL lives in `db/rls.py` (applied by `opsdesk migrate`, idempotent). The audit log is append-only by trigger.

**Channels → one envelope → one router.** Adapters (`opsdesk/channels/adapters/`, registered via `channel_adapters` in `settings.py`) take a framework-neutral `InboundRequest` and normalise into `InboundEnvelope`; one router decides new ticket vs reply, so SLA, automation, AI and audit are uniform. Inbound webhooks write an `InboundMessage` outbox row processed by Celery (lease / `SKIP LOCKED`).

**Config-driven workflow.** Statuses, transitions, SLAs, custom fields, priority matrix and automation rules are DB rows edited in the console, not code. State `category` (not name) drives reports/SLA/UI logic. `services/bootstrap.install_defaults` seeds a tenant. Side effects (Celery dispatch, notifications) run after commit via `services/commit_hooks.py`.

**Roles:** `super_admin` (exactly one account for the whole site, enforced by the unique index `core_user_single_super_admin`; the only role that can open Administration (users, teams, SLA, Configuration, channels, audit log): `ADMIN_ROLES` in `db/models/core.py`, enforced by the `require_administration` dependency on the admin routers and mirrored by `isAdminRole` in `src/roles.js`. Everything else matches `manager`, and it is the place for future owner-only features. No role can create, edit, deactivate, reset or assign it through the API: `admin_rules.check_not_super_admin`; it is created and recovered only with `opsdesk create-user --role super_admin` / `reset-password`), `manager` (top role: configuration, SLA, audit log, passwords), `team_lead` (sees all tickets, manages users' profile details and teams), `employee` (works tickets; sees only tickets assigned to them, raised by them or where they are the requester: `OWN_TICKET_ROLES`, enforced in `services/visibility.py` and mirrored in the raw SQL of `api/repositories/tickets.py`) and `customer` (external). The first three are "staff" (`is_agent` in the API and UI). Role groups (`TOP_ROLES`, `PRIVILEGED_ROLES`, `STAFF_ROLES`, `effective_roles` for workflow transitions) are defined once in `db/models/core.py`, mirrored in `api/admin_rules.py` and `src/roles.js`: use them instead of listing role names, so a role is added in one place.

**UI feature switches:** `src/features.js` hides Service catalog, Approvals, CMDB Explorer (menus, command palette, routes, the ticket Approvals card and their Configuration sections) and Save Queue on the ticket list without removing any code, API or data. Flip a flag to `true` and rebuild the web image to bring one back.

**Frontend** (`src`): `App.jsx` shell + `navModel.js` (role-based nav/routes), `pages/` (each feature has a `*Helpers.js` with pure logic and a `.test.js`), `components/`.

## Repo conventions worth knowing

- Root `ui_*`, `u6_*`, `post_u6_*`, `backend_*`, `pre_u6_*` directories and the root `*.md` stage reports are **dated evidence/history** from past work stages and describe earlier implementation stages. They are not application code; do not edit or "fix" them.
- `PROMPT_TRACKER.md` is append-only (do not rewrite earlier rows).
- Migrations are additive by convention; split destructive changes across releases.
- `beat` must run as exactly one instance.
- Settings are env-driven in `opsdesk/settings.py`; production fails closed when `JWT_SECRET_KEY` or the signing key is missing (a dev key is allowed only with `OPSDESK_DEBUG`/testing).
