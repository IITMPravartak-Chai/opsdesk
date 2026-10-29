# OpsDesk: cloud IT service management

OpsDesk is an internet-facing, multi-tenant IT ticket management system for teams that support many customers' applications, AI systems and platforms. It is built around ITIL practices and includes a channel plug-in architecture, configurable workflows and SLAs, and an AI engine that works offline and gets better with an LLM.

**Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 + Alembic, PostgreSQL 16 + pgvector + pg_trgm, Redis + Celery, React 18 PWA (web + installable mobile app), nginx. Everything ships as two container images.

For cloud deployment see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## Contents

1. [What's included](#whats-included)
2. [Architecture](#architecture)
3. [Quick start with Docker](#quick-start-with-docker)
4. [Local development without Docker](#local-development-without-docker)
5. [Configuring the service desk](#configuring-the-service-desk)
6. [Channels and how to add a new one](#channels-and-how-to-add-a-new-one)
7. [AI engine](#ai-engine)
8. [Data model](#data-model)
9. [API reference](#api-reference)
10. [Security model](#security-model)
11. [Tests](#tests)
12. [Known limits and roadmap](#known-limits-and-roadmap)

---

## What's included

### ITIL practices

| Practice | Implementation |
|---|---|
| Incident management | Incident workflow, impact × urgency priority matrix, major incidents with parent/child tickets, auto-assignment, escalation rules |
| Service request management | Request workflow with approval step, custom request forms per type |
| Problem management | Problem workflow (root-cause analysis → known error → resolved), linking incidents to problems |
| Change enablement | Change workflow (draft → assessment → CAB review → scheduled → implementing → PIR), mandatory risk/back-out fields, approvals gate |
| Service level management | SLA policies per account or default, business-hours calendars with holidays and timezones, pause states, at-risk and breach detection, escalation |
| Service configuration management | CMDB-lite: services and configuration items (applications, AI/ML models, LLM apps, data pipelines, platforms) with dependency graph and downstream impact |
| Knowledge management | KB with internal/public articles, "turn this resolved ticket into an article", suggestions on tickets and while customers type |
| Monitoring and event management | Webhook channel for Alertmanager/Grafana/Datadog/Azure Monitor with fingerprint de-duplication and optional auto-resolve |
| Service desk | Omnichannel intake (email, WhatsApp, portal, mobile, alerts, API), canned responses, internal notes, CSAT surveys |
| Measurement and reporting | SLA compliance, MTTR, first response time, backlog ageing, account hotspots, channel mix, CSAT, AI-suggestion acceptance |

### Everything is configurable (no code)

Statuses, transitions, which roles may perform them, required fields, mandatory comments, approval gates, SLA pause states, ticket types, number prefixes, custom form fields, priorities, the priority matrix, SLA targets, business hours, holidays, teams, assignment strategies, categories, channels and automation rules are all edited in the configuration console at `/admin/`.

### AI engine

- **Similar tickets**: hybrid semantic (pgvector) + lexical (trigram) search, showing how past tickets were fixed.
- **Duplicate detection**: open tickets are flagged with one-click merge.
- **Triage suggestions**: category, team, impact and urgency, via a nearest-neighbour vote or an LLM.
- **Ticket deflection**: articles and past fixes appear while a user types a new request.
- **Summaries and draft replies**: these need an LLM; there is a grounded fallback without one.
- **PII and secret redaction**: applied before any text leaves for an external model.
- **Acceptance tracking**: measures suggestion precision before you enable auto-apply.

### Value-added features

- No-code automation rules: event → conditions → actions (assign, set priority, tag, transition, notify, signed webhook).
- Round-robin and least-loaded assignment, with an agent availability toggle.
- Billable time tracking per ticket, useful for invoicing customers.
- CSAT surveys sent on resolution, plus auto-close of resolved tickets after N days.
- Auto-reopen when a customer replies to a pending or resolved ticket.
- Ticket links, merge and parent/child relationships.
- Approvals for changes and requests.
- Attachments with type and size limits, ClamAV scanning, and private S3 storage with short-lived presigned downloads.
- TOTP two-step sign-in, an append-only audit log, and PostgreSQL row-level security.

---

## Architecture

```mermaid
flowchart LR
  subgraph Channels
    E[Email<br/>webhook or IMAP]
    W[WhatsApp<br/>Cloud API]
    M[Monitoring alerts<br/>webhook]
    P[Web portal / PWA]
  end
  E & W & M -->|/api/channels/inbound/&lt;token&gt;/| A
  P -->|REST /api| A
  subgraph Platform
    N[nginx + PWA] --> A[API<br/>FastAPI]
    A --> Q[(Redis)]
    Q --> K[Celery worker<br/>ingest, AI, delivery, email]
    B[Celery beat<br/>SLA monitor, polling, auto-close] --> Q
    A & K --> D[(PostgreSQL 16<br/>pgvector, RLS)]
    A & K --> S[(Object storage<br/>attachments)]
  end
  K -->|optional| L[LLM / embeddings API]
```

Every channel normalises messages into one `InboundEnvelope`. A single router then decides whether the message is a new ticket or a reply, so SLA, automation, AI and audit behave the same everywhere.

```
opsdesk/
├── src/, public/, index.html   React PWA (Vite)
├── package.json, vite.config.js
├── nginx.conf, Dockerfile      web image (nginx + built PWA)
├── backend/
│   ├── opsdesk/settings.py     all env-driven configuration
│   ├── opsdesk/db/             SQLAlchemy models (one file per area), sessions, row-level-security SQL
│   ├── opsdesk/security/       password hashing, JWT, sign-in throttling
│   ├── opsdesk/services/       tickets, SLA engine, workflow, automation, notifications, reports, bootstrap, demo seed
│   ├── opsdesk/channels/       adapter contract, registry, router, OAuth, email/WhatsApp/webhook adapters
│   ├── opsdesk/ai/             embedders, LLM providers, redaction, similarity/triage/summary engine
│   ├── opsdesk/api/            FastAPI app, routers (tickets, catalog, admin, cmdb, kb, reports ...), schemas
│   ├── opsdesk/worker/         Celery app, tasks and beat schedule
│   ├── opsdesk/cli.py          operations CLI (migrate, create-tenant, seed-demo, keys ...)
│   ├── alembic/                schema migrations
│   ├── sql/00_init_roles.sql   database roles
│   ├── tests/                  pytest suite (real PostgreSQL + Redis)
│   └── Dockerfile, entrypoint.sh, requirements.txt
├── docker-compose.yml
├── .env.example
└── DEPLOYMENT.md
```

---

## Quick start with Docker

Requires Docker with Compose v2.

```bash
cp .env.example .env
# Set JWT_SECRET_KEY, FIELD_ENCRYPTION_KEYS and OPSDESK_SIGNING_KEY_ID / OPSDESK_SIGNING_KEY_SECRET:
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build                    # db, redis, migrate, api, worker, beat, web, mailpit
docker compose run --rm api seed-demo           # optional: demo tenant with realistic data
```

To rebuild the demo later, run `seed-demo --reset` with the owner credentials (`DATABASE_URL=$MIGRATION_DATABASE_URL`), because the reset briefly disables the audit-log trigger.

| URL | Purpose |
|---|---|
| http://localhost:8080 | Agent console and customer portal (installable as an app on phones) |
| http://localhost:8080/settings/config | Configuration console (admin and manager) |
| http://localhost:8025 | Mailpit: every email the system sends |

**Demo logins** (password `Demo!Passw0rd2026`):

| Email | Role |
|---|---|
| superadmin@demo.opsdesk.dev | Super admin: the single, locked site-owner account (manager-level access for now; no other role can edit it) |
| manager@demo.opsdesk.dev | Manager (configuration console, SLA, audit log) |
| teamlead@demo.opsdesk.dev | Team lead |
| priya@demo.opsdesk.dev, arjun@demo.opsdesk.dev | L1 employees |
| meera@demo.opsdesk.dev | Cloud platform employee |
| david@demo.opsdesk.dev | AI/ML ops employee |
| sam@globex.com, lena@initech.com, ravi@umbrella-health.com | Customers (one per account) |

**Create a real tenant instead of the demo:**

```bash
docker compose run --rm api create-tenant --name "Your Company" --slug yourco \
  --admin-email you@yourco.com --timezone Asia/Kolkata
```

### Try the channels against the demo

```bash
# Email (generic JSON inbound format)
curl -X POST "http://localhost:8080/api/channels/inbound/demo-email-inbound-0001/?token=demo-email-secret" \
  -H "Content-Type: application/json" \
  -d '{"from":"Sam Porter <sam@globex.com>","subject":"Checkout throwing 502","text":"Customers see 502 at checkout","message_id":"<t1@globex.com>"}'

# Monitoring alert (Prometheus Alertmanager format). Send twice: one ticket, two occurrences.
curl -X POST http://localhost:8080/api/channels/inbound/demo-alerts-inbound-0001/ \
  -H "Authorization: Bearer demo-alert-secret" -H "Content-Type: application/json" \
  -d '{"alerts":[{"status":"firing","fingerprint":"fp1","startsAt":"2026-10-09T10:00:00Z",
       "labels":{"severity":"critical","customer":"INTB","ci":"Payments API"},
       "annotations":{"summary":"Payments API error rate > 5%","description":"5xx ratio 7.2% for 10m"}}]}'
```

The demo WhatsApp channel has placeholder credentials. See DEPLOYMENT.md section 12 to connect a real number.

---

## Local development without Docker

```bash
# PostgreSQL 16 with pgvector, and Redis, running locally
psql -U postgres -c "CREATE DATABASE opsdesk"
psql -U postgres -d opsdesk -f backend/sql/00_init_roles.sql

cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export OPSDESK_DEBUG=1 CELERY_TASK_ALWAYS_EAGER=1 USE_LOCAL_CACHE=1 DB_APP_ROLE=opsdesk_app
export MIGRATION_DATABASE_URL=postgres://opsdesk_owner:CHANGE_ME_owner@localhost:5432/opsdesk
export DATABASE_URL=postgres://opsdesk_app:CHANGE_ME_app@localhost:5432/opsdesk
export PYTHONPATH=$PWD
python -m opsdesk migrate            # Alembic upgrade + row-level-security policies (uses the owner URL)
python -m opsdesk seed-demo
uvicorn opsdesk.api.main:app --port 8001 --reload

cd .. && npm install && npm run dev      # http://localhost:5173, proxies /api to :8001
```

`CELERY_TASK_ALWAYS_EAGER=1` runs background jobs inline, so you don't need a worker during development.

---

## Configuring the service desk

All configuration lives in the **Configuration** console (`/settings/config`, plus the SLA, Users and Audit pages; click a channel in Configuration → Channels for its webhook URL, routing and mailbox connection) and is visible only to admin and manager roles, scoped to their own tenant. `create-tenant` installs a complete ITIL starter configuration that you then adapt.

### Workflows (statuses and flow)

Open **Workflows → Incident**:

- **States**: each has a `key`, a display `name`, and a `category`. The category (new / open / pending / resolved / closed / cancelled) is what reports, SLA logic and the UI rely on, so you can name states anything.
  - Tick **Pauses SLA** for states like *Waiting on customer*.
  - Mark exactly one state **Is initial**.
- **Transitions**: each becomes a button in the ticket view.
  - `from_state` can be empty, which means from any open state.
  - `allowed_roles`, e.g. `["team_lead","manager"]`; leave empty for any staff role.
  - `customer_allowed`: shows the button to requesters in the portal, e.g. *Reopen* or *Confirm and close*.
  - `requires_comment`.
  - `required_fields`: ticket fields such as `resolution_notes`, or custom-field keys such as `backout_plan`.
  - `requires_approval`: all requested approvals must be approved first.
- **Customer reply state**: where a pending or resolved ticket goes when the customer replies.

### Ticket types and forms

**Ticket types** set the number prefix, which workflow to use, default impact/urgency, and whether customers can raise that type. **Custom fields** (inline on the type) render automatically in the portal, agent console and transition dialogs. They support text, long text, number, select, multi-select, date and yes/no, with "visible to customer" control.

### SLAs

- **Business hours**: timezone, weekly schedule (`{"0": [["09:00","18:00"]], ...}`, 0 = Monday), and a holiday list. Tick 24x7 for round-the-clock coverage.
- **SLA policies**: first-response and resolution minutes per priority, measured in the calendar's working minutes. You also set the at-risk threshold (default 75%) and which ticket types the policy applies to.
- Assign a policy to an **Account** for premium customers; otherwise the default policy applies.
- **Priority matrix**: impact × urgency → priority (ITIL style). Agents can still override priority, which recalculates due dates.

### Teams and assignment

A team covers some or all accounts. Its assignment strategy can be *manual*, *round robin* or *least loaded*; agents marked unavailable are skipped. Categories, services and CIs can each have a default or owner team for routing.

### Automation rules

**Rules** run on `ticket.created`, `ticket.updated`, `ticket.state_changed`, `ticket.customer_replied`, `sla.at_risk` and `sla.breached`. Example: route anything touching an AI system to the ML team.

```json
conditions: [{"field": "ci.ci_type", "op": "in", "value": ["ai_model", "llm_app", "data_pipeline"]}]
actions:    [{"type": "assign_team", "value": "AI/ML ops"}, {"type": "add_tag", "value": "ai"}]
```

- Operators: `eq ne in not_in contains not_contains gt lt is_empty not_empty changed`.
- Fields: any dotted path, e.g. `priority.key`, `account.tier`, `source`, `custom_fields.region`.
- Actions: `assign_team`, `assign_user`, `set_priority`, `add_tag`, `set_field`, `transition_to`, `set_major_incident`, `add_note`, `notify` (to `assignee`, `team`, `requester` or addresses), and `webhook` (HMAC-signed with `X-OpsDesk-Signature`).

### Tenant settings

These are set on **Tenant → settings** (JSON) by a platform superuser:

- `auto_close_days` (default 5).
- `customer_sees_account_tickets` (default true).
- `csat_enabled`.
- `auto_apply_triage` (default false) and `triage_confidence_threshold` (0.8). Turn auto-apply on only after the AI acceptance rate on the dashboard is consistently high.

---

## Channels and how to add a new one

| Kind | Inbound | Outbound replies | Notes |
|---|---|---|---|
| `email` | Provider webhook (SendGrid Inbound Parse, or generic JSON from SES/Lambda, Mailgun, Power Automate) | SMTP/SES with `[INC-000123]` subject and threading headers | Quoted text stripped; sender domain maps to account |
| `imap` | Polls a mailbox every 2 minutes | Same as email | Easiest with Microsoft 365 / Google Workspace shared inboxes |
| `whatsapp` | Meta webhook (signed) | Free text inside 24h window, approved template outside | Media downloaded, delivery/read receipts tracked, 72h conversation threading |
| `webhook` | Any JSON (alerting, MLOps, partner systems) with field mapping | Not applicable | Fingerprint correlation, severity → impact/urgency, optional auto-resolve |
| Portal / mobile | REST API | Email notification to requester | Live deflection suggestions |

Each channel gets an unguessable inbound URL, `/api/channels/inbound/<token>/`, shown on the channel's admin page. Secrets in `config` are encrypted at rest. Every inbound message is stored idempotently (`InboundMessage`), so provider retries never create duplicates, and failed messages can be reprocessed from the admin.

### Adding a channel (example: Microsoft Teams or Slack)

1. **Write an adapter** anywhere importable, e.g. `backend/opsdesk/channels/adapters/slack.py`:

```python
import hashlib, hmac, json, time, requests
from ..base import ChannelAdapter, InboundEnvelope, ParseResult

class SlackAdapter(ChannelAdapter):
    kind = "slack"
    label = "Slack (Events API)"
    source = "other"                      # or add "slack" to tickets.models.SOURCES
    config_help = {"signing_secret": "Slack app signing secret", "bot_token": "xoxb-..."}

    def handshake(self, request):          # Slack URL verification
        if request.method == "POST":
            body = json.loads(request.body or b"{}")
            if body.get("type") == "url_verification":
                return HandshakeResponse(200, json.dumps({"challenge": body["challenge"]}), "application/json")
        return None

    def verify(self, request):
        ts = request.headers.get("X-Slack-Request-Timestamp", "0")
        if abs(time.time() - int(ts)) > 300:
            return False
        base = f"v0:{ts}:{request.body.decode()}".encode()
        sig = "v0=" + hmac.new(self.config["signing_secret"].encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, request.headers.get("X-Slack-Signature", ""))

    def parse(self, request):
        ev = json.loads(request.body)["event"]
        if ev.get("bot_id"):
            return ParseResult()
        return ParseResult([InboundEnvelope(
            external_id=ev["client_msg_id"], body=ev.get("text", ""),
            identity_kind="slack", identity_value=ev["user"],
            in_reply_to=[ev["thread_ts"]] if ev.get("thread_ts") else [],
        )])

    def send(self, ticket, interaction):
        r = requests.post("https://slack.com/api/chat.postMessage",
                          headers={"Authorization": f"Bearer {self.config['bot_token']}"},
                          json={"channel": self.settings["slack_channel"], "text": interaction.body})
        return r.json().get("ts", "")
```

2. **Register it**: add `"slack": "opsdesk.channels.adapters.slack.SlackAdapter"` to `channel_adapters` in `opsdesk/settings.py`. For plug-ins outside this repo, set `EXTRA_CHANNEL_ADAPTERS=slack=yourpkg.SlackAdapter` instead, with no code change.
3. **Create the channel** in Configuration → Channels (kind `slack`), paste credentials into `config`, and copy the generated inbound URL into the provider's console.

For pull-based sources (mailboxes, a partner's ITSM API) set `is_pull = True` and implement `poll()`; the scheduler calls it every 2 minutes. Identity matching, threading, ticket creation, SLA, rules, AI and replies need no changes.

---

## AI engine

| Capability | Without an LLM | With an LLM (`AI_LLM_PROVIDER`) |
|---|---|---|
| Similar tickets, duplicates, KB suggestions, deflection | Yes (embeddings + trigram) | Same |
| Triage suggestion | Weighted vote of nearest resolved tickets | LLM classification grounded on your categories, teams and similar tickets |
| Summary | Extractive | Abstractive handover summary |
| Draft reply | Template grounded on the best past fix or KB article | LLM draft grounded on past fixes and KB |

- **Embeddings** default to a local hashing embedder: zero cost, works offline, good on IT vocabulary. For better semantic recall, set `AI_EMBEDDING_PROVIDER=openai` with any OpenAI-compatible endpoint (OpenAI, Azure OpenAI v1, vLLM/Ollama, a LiteLLM gateway) that supports 384 dimensions, then run `python manage.py reindex_embeddings`.
- **Thresholds** are calibrated per embedder. Tune them with `AI_DUPLICATE_THRESHOLD` and `AI_SIMILAR_MIN_SCORE` once you have real data.
- **Redaction**: emails, phone numbers, API keys, JWTs, passwords, card numbers, Aadhaar, PAN and IP addresses are masked before any external call (`AI_REDACT_PII=1`).
- **Governance**: suggestions are advisory by default. Accept/reject actions are logged in `AIFeedback`, and the dashboard shows the acceptance rate. Use that evidence before enabling `auto_apply_triage`.

---

## Data model

All tenant-owned tables carry `tenant_id` and are protected by row-level security.

| Area | Models |
|---|---|
| Tenancy and people | `Tenant`, `User` (role: super_admin / manager / team_lead / employee / customer; MFA), `Account` (customer organisation, tier, email domains, SLA policy), `Team`, `ContactIdentity` (email / WhatsApp identities → user), `BusinessHours`, `AuditLog` |
| Configuration | `Workflow`, `State`, `Transition`, `TicketType`, `CustomField`, `Priority`, `PriorityMatrix`, `Category`, `SLAPolicy`, `SLATarget` |
| CMDB | `Service`, `ConfigurationItem` (types incl. `ai_model`, `llm_app`, `data_pipeline`; `attributes` JSON for model metadata; `depends_on` graph) |
| Tickets | `Ticket` (SLA clock fields, `custom_fields` JSONB, `tags` array, `embedding vector(384)`, AI triage JSON), `Interaction` (public / internal / system, delivery status), `Attachment`, `TicketEvent` (history), `TicketLink`, `Approval`, `TimeEntry`, `CannedResponse`, `CSATSurvey`, `TicketSequence` |
| Channels | `Channel` (kind, encrypted `config`, routing `settings`, inbound token), `InboundMessage` (idempotency, envelope, status) |
| Knowledge and AI | `KnowledgeArticle` (with embedding), `AIFeedback` |
| Automation | `Rule` |

Indexes include HNSW (cosine) on embeddings, a GIN full-text index on tickets, and composite tenant indexes. The schema is defined by the SQLAlchemy models in `backend/opsdesk/db/models/` and the Alembic revisions in `backend/alembic/versions/`. `python -m opsdesk migrate` applies them; `alembic upgrade head --sql` prints the SQL.

---

## API reference

All endpoints are under `/api/` and take JWT `Authorization: Bearer <access>` unless noted.

| Endpoint | Purpose |
|---|---|
| `POST auth/login/` (public) | `{email, password, otp?}` → access/refresh tokens |
| `POST auth/refresh/`, `GET/PATCH auth/me/`, `POST auth/mfa/` | Session, profile/availability, MFA enrolment |
| `GET meta/` | Everything the UI needs to render forms |
| `GET/POST tickets/` | List (filters: `q, open, state_category, priority, assignee=me\|none\|id, team, account, type, sla_status, source, major, mine, ordering`) / create |
| `GET/PATCH tickets/{id}/` | Detail (includes available transitions) / update fields |
| `POST tickets/{id}/transition/` | `{transition_id, comment?, fields?, comment_public?}` |
| `GET/POST tickets/{id}/interactions/` | Conversation; multipart for attachments |
| `POST tickets/{id}/attachments/`, `GET attachments/{id}/download/` | Files |
| `POST tickets/{id}/link/`, `merge/`, `approvals/`, `approvals/{aid}/decide/`, `GET/POST time/`, `watch/`, `GET events/` | Relationships, approvals, time, history |
| `GET tickets/{id}/similar/`, `kb_suggestions/`; `POST ai_summary/`, `ai_draft/`, `ai_triage/`, `apply_triage/` | AI |
| `GET tickets/counts/` | Queue badge counts |
| `POST ai/search/`, `POST ai/feedback/` | Deflection search, suggestion feedback |
| `GET/POST kb/articles/`, `POST kb/articles/from_ticket/`, `POST kb/articles/{id}/vote/` | Knowledge |
| `GET cmdb/services/`, `cmdb/items/`, `cmdb/items/{id}/impact/` | CMDB |
| `GET reports/overview/?days=30&account=` | KPIs |
| `GET canned-responses/`, `GET canned-responses/{id}/render/?ticket=` | Canned replies |
| `GET channels/` | Channel list with inbound URLs (managers) |
| `GET/POST channels/inbound/{token}/` (public, signed) | Channel webhooks |
| `GET/POST csat/{token}/` (public) | Survey |
| `GET /healthz` (public) | Liveness/readiness (checks DB) |

---

## Security model

- **Tenant isolation**, two layers: every query is filtered by tenant in the application, and PostgreSQL row-level security enforces the same rule in the database. The app connects as a non-owner role, so even a missed filter returns nothing.
  - System work that crosses tenants (resolving a webhook token to its channel, schedulers) runs in an explicit `system_context()`.
  - `core_user` is deliberately outside RLS because login happens before a tenant is known.
- **Row visibility**: customers see their own account's tickets and only public replies. Agents see the accounts their teams cover. Managers and admins see the whole tenant.
- **Authentication**: 15-minute access tokens, rotating refresh tokens with a blacklist, TOTP two-step sign-in, login throttling shared via Redis, and a 12-character minimum password.
- **Channels**: every webhook is authenticated (HMAC signatures for WhatsApp/webhook, shared token for email). Inbound URLs contain an unguessable token. Credentials are encrypted with Fernet and support key rotation.
- **Data**: the audit log is append-only (a DB trigger blocks UPDATE/DELETE). Attachments get type/size limits, an optional ClamAV scan and private storage with 5-minute presigned links. PII is redacted before any LLM call.
- **Web**: HSTS, secure cookies, a strict CSP and anti-framing headers from nginx, and a non-root container user.

DEPLOYMENT.md section 16 maps these controls to ISO/IEC 27001:2022 Annex A and OWASP ASVS.

---

## Tests

```bash
cd backend
export PYTHONPATH=$PWD
python -m pytest                # needs PostgreSQL (CREATEDB role, pgvector) and Redis; see tests/conftest.py
```

The backend suite (about 500 tests, built on a real PostgreSQL created through Alembic) covers:
- Business-hours SLA arithmetic (weekend, holiday, inverse property, 24x7).
- Workflow guards (required fields, role/customer permissions, approvals).
- SLA pause/resume, auto-reopen on customer reply, and first-response recording.
- Automation routing.
- Email ingest, threading, quote stripping, idempotency and cross-account protection.
- WhatsApp handshake, signature, threading and receipts.
- Alert correlation and auto-resolve.
- API visibility for customers versus staff.
- Similarity ranking, the dashboard, and the transition API.

---

## Known limits and roadmap

These are deliberate scope boundaries for v1:

- **Enterprise SSO** (SAML/OIDC per customer) is not built yet. Add an OIDC client library (for example Authlib) and issue the same JWTs after the IdP callback.
- **Push notifications** for the mobile PWA are not built. Web Push needs VAPID keys and a subscription table. For App Store/Play Store distribution, wrap the PWA with Capacitor; the codebase doesn't change.
- **Webhook rate limiting**: inbound webhooks are plain FastAPI routes. Rate-limit them at the WAF or load balancer (rules are in DEPLOYMENT.md).
- **Knowledge articles** are edited in the configuration console and the API. A rich in-app editor and full Markdown rendering are next.
- **Reporting** is an operational dashboard. For contract SLA reports per customer, point a BI tool at a read replica using the same RLS-protected role.
- **Language**: the UI is English-only; strings are centralised in the React pages, ready for i18n.
