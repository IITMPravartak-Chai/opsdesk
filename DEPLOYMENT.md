# OpsDesk deployment guide

This guide takes you from an empty cloud account to a production, internet-facing OpsDesk. AWS is the reference platform, with Azure and GCP equivalents in [section 3](#3-azure-and-gcp-equivalents). All configuration is environment variables, so the same images run anywhere that runs containers.

---

## 1. Reference architecture

```mermaid
flowchart TB
  U[Agents / customers<br/>browser + PWA] --> CF[DNS + TLS<br/>Route 53, ACM]
  CF --> WAF[AWS WAF]
  WAF --> ALB[Application Load Balancer]
  META[Meta WhatsApp] & MAIL[Email provider] & MON[Monitoring tools] --> WAF
  subgraph VPC [VPC: 2 AZs, private subnets]
    ALB -->|/api*, /healthz| API[ECS service: api<br/>uvicorn]
    ALB -->|everything else| WEB[ECS service: web<br/>nginx + PWA]
    API --> RDS[(RDS PostgreSQL 16<br/>Multi-AZ, pgvector)]
    API --> REDIS[(ElastiCache Redis)]
    WORKER[ECS service: worker<br/>Celery] --> RDS & REDIS
    BEAT[ECS service: beat<br/>exactly 1 task] --> REDIS
    API & WORKER --> S3[(S3: attachments<br/>private, SSE-KMS)]
    CLAM[ECS service: clamav<br/>optional] --- API & WORKER
  end
  WORKER --> SES[Amazon SES / SMTP]
  WORKER --> GRAPH[WhatsApp Cloud API]
  WORKER -.optional.-> LLM[LLM / embeddings API]
  API & WORKER --> SM[Secrets Manager]
  API & WORKER --> CW[CloudWatch logs + alarms]
```

**Sizing to start.** The figures below suit roughly 50 agents, 20 customer accounts and 10,000 tickets a month. Scale horizontally from there.

| Component | Starting size | Scale signal |
|---|---|---|
| api | 2 tasks × 0.5 vCPU / 1 GB, `API_WORKERS=3` | CPU > 60%, p95 latency |
| worker | 2 tasks × 0.5 vCPU / 1 GB, `CELERY_CONCURRENCY=4` | Redis queue depth, inbound processing lag |
| beat | **exactly 1** task × 0.25 vCPU / 0.5 GB | never scale (duplicate schedules) |
| web | 2 tasks × 0.25 vCPU / 0.5 GB | rarely needed |
| RDS PostgreSQL 16 | db.m7g.large Multi-AZ, gp3 100 GB autoscaling | CPU, connections, IOPS |
| ElastiCache Redis 7 | cache.t4g.small, 1 replica | memory, evictions |
| S3 | standard, lifecycle to IA after 90 days | none |

For India-based customers or DPDP Act data-residency commitments, deploy in **ap-south-1 (Mumbai)** and keep backups in-region, or use ap-south-2 (Hyderabad) for DR.

---

## 2. Resources to create (checklist)

| # | Resource | Key settings |
|---|---|---|
| 1 | VPC | 2 AZs; public subnets for ALB/NAT; private subnets for ECS, RDS, Redis; VPC endpoints for S3, ECR, Secrets Manager, CloudWatch Logs |
| 2 | Security groups | `alb-sg` (443 from internet), `app-sg` (8000/8080 from alb-sg), `db-sg` (5432 from app-sg), `redis-sg` (6379 from app-sg), `clam-sg` (3310 from app-sg) |
| 3 | RDS PostgreSQL 16 | Multi-AZ, encryption with KMS, `rds.force_ssl=1`, backup retention 14-35 days, Performance Insights, deletion protection |
| 4 | ElastiCache Redis 7 | In-transit and at-rest encryption, AUTH token |
| 5 | S3 bucket `opsdesk-attachments-<env>` | Block all public access, SSE-KMS, versioning, lifecycle rules, bucket policy denying non-TLS |
| 6 | ECR repositories | `opsdesk-api`, `opsdesk-web`; scan on push; immutable tags |
| 7 | Secrets Manager | Section 6 |
| 8 | IAM | Task execution role (pull ECR, read secrets, write logs); task role (S3 access below; SES send if using the API) |
| 9 | ECS cluster (Fargate) | Services: api, worker, beat, web, optional clamav; Cloud Map namespace `opsdesk.local` |
| 10 | ALB + ACM certificate | HTTPS listener, HTTP→HTTPS redirect, path rules (section 9) |
| 11 | AWS WAF | Managed rule groups and rate rules (section 9) |
| 12 | Route 53 | `desk.yourco.com` alias to ALB; SES DNS records |
| 13 | Amazon SES | Verified domain, DKIM, custom MAIL FROM, production access (out of sandbox) |
| 14 | CloudWatch | Log groups (30-90 day retention), alarms (section 13) |

S3 policy for the task role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
     "Resource": "arn:aws:s3:::opsdesk-attachments-prod/*"},
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::opsdesk-attachments-prod"},
    {"Effect": "Allow", "Action": ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"],
     "Resource": "arn:aws:kms:ap-south-1:<acct>:key/<attachments-key-id>"}
  ]
}
```

With `S3_SSE=aws:kms` the app requests KMS encryption. Alternatively, set the bucket default encryption to SSE-KMS and leave `S3_SSE=AES256`.

We recommend codifying the above in Terraform or CDK once it settles, so staging and production are identical (ISO 27001 A.8.9 configuration management).

---

## 3. Azure and GCP equivalents

| Role | AWS | Azure | GCP |
|---|---|---|---|
| Containers | ECS Fargate | Azure Container Apps | Cloud Run (api, web) + Cloud Run worker pool or GKE Autopilot (worker, beat) |
| PostgreSQL + pgvector | RDS for PostgreSQL 16 | Azure Database for PostgreSQL Flexible Server (allow-list `VECTOR`, `PG_TRGM` extensions) | Cloud SQL for PostgreSQL 16 |
| Redis | ElastiCache | Azure Cache for Redis | Memorystore for Redis |
| Object storage | S3 | Blob Storage (use the S3-compatible gateway) | GCS with HMAC keys and `S3_ENDPOINT_URL=https://storage.googleapis.com` |
| Secrets | Secrets Manager | Key Vault | Secret Manager |
| Load balancer + WAF | ALB + AWS WAF | Front Door + WAF policy | External HTTPS LB + Cloud Armor |
| Email | SES | Azure Communication Services Email | SendGrid / Mailgun via SMTP |
| Registry | ECR | ACR | Artifact Registry |
| Logs / alarms | CloudWatch | Azure Monitor / Log Analytics | Cloud Logging / Monitoring |

On Cloud Run, the `beat` role must run with min = max = 1 instance and CPU always allocated. The `worker` role needs CPU always allocated too, or move both to GKE.

---

## 4. Database setup (once per environment)

1. Create the RDS instance with database name `opsdesk`.
2. From a bastion or ECS Exec session, connect as the master user and run [`backend/sql/00_init_roles.sql`](backend/sql/00_init_roles.sql), after replacing both passwords with values from Secrets Manager:

   ```bash
   psql "host=<rds-endpoint> dbname=opsdesk user=<master> sslmode=require" -f backend/sql/00_init_roles.sql
   ```

   This script:
   - creates **`opsdesk_owner`**, which owns the schema and runs migrations;
   - creates **`opsdesk_app`**, which the application uses and which is subject to row-level security;
   - creates the `vector` and `pg_trgm` extensions. pgvector is supported on RDS PostgreSQL 15.2+ and 16.

3. Never give `opsdesk_app` ownership or `BYPASSRLS`, and never point `DATABASE_URL` at the owner role. Owners bypass RLS.

**Connection pooling: important.** Tenant isolation uses session settings (`app.tenant_id`), which are set on every request and every background task. Use **direct connections or PgBouncer in session mode**. Do **not** put a transaction-mode pooler (PgBouncer transaction mode, or RDS Proxy multiplexing) between the app and the database: session variables could leak between clients. Size connections as:

```
api tasks × API_WORKERS × (FASTAPI_DB_POOL_MAX + 2) + worker tasks × CELERY_CONCURRENCY + 10
```

For example, 2×3×4 + 2×4 + 10 = 42, well within db.m7g.large's limits.

---

## 5. Build and push images

```bash
AWS_REGION=ap-south-1
REG=<acct>.dkr.ecr.$AWS_REGION.amazonaws.com
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REG

docker build -t $REG/opsdesk-api:$TAG backend
docker build -t $REG/opsdesk-web:$TAG .
docker push $REG/opsdesk-api:$TAG
docker push $REG/opsdesk-web:$TAG
```

Build for `linux/arm64` (`docker buildx build --platform linux/arm64`) if you run Fargate on Graviton, which is cheaper. The api image runs every backend role:

| Command | Role |
|---|---|
| `api` | uvicorn API (port `API_PORT`, default 8001) |
| `worker` | Celery worker |
| `beat` | Celery scheduler |
| `migrate` | Migrations + RLS + grants |
| anything else | `opsdesk <args>` (the operations CLI: `create-tenant`, `create-user`, `reset-password`, `seed-demo`, `keys`, `tenants`, `check` ...) |

---

## 6. Secrets and environment

Store these in Secrets Manager and inject them as ECS secrets. Plain environment variables are fine for non-secrets. Every variable is documented in [`.env.example`](.env.example).

| Variable | Secret? | Production value |
|---|---|---|
| `JWT_SECRET_KEY` | yes | 50+ random characters; rotating it signs everyone out |
| `OPSDESK_SIGNING_KEY_ID`, `OPSDESK_SIGNING_KEY_SECRET` | yes | tenant-context signing key (`opsdesk keys add <id>` prints a pair); required, there is no default |
| `FIELD_ENCRYPTION_KEYS` | yes | Fernet key(s), newest first; encrypts channel credentials |
| `DATABASE_URL` | yes | `postgres://opsdesk_app:<pw>@<rds>:5432/opsdesk` |
| `MIGRATION_DATABASE_URL` | yes | `postgres://opsdesk_owner:<pw>@<rds>:5432/opsdesk`; give it **only** to the migrate task |
| `DB_APP_ROLE` | no | `opsdesk_app` |
| `DB_REQUIRE_SSL` | no | `1` |
| `REDIS_URL` | yes | `rediss://:<auth-token>@<redis-endpoint>:6379/0` |
| `PUBLIC_BASE_URL`, `CORS_ALLOWED_ORIGINS` | no | `https://desk.yourco.com` |
| `SECURE_SSL_REDIRECT` | no | `1` |
| `STORAGE_BACKEND`, `S3_BUCKET`, `S3_REGION`, `S3_SSE` | no | `s3`, bucket, `ap-south-1`, `aws:kms` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` | no | `email-smtp.ap-south-1.amazonaws.com`, `587`, `1` |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | yes | SES SMTP credentials |
| `DEFAULT_FROM_EMAIL` | no | `Your Co Support <support@yourco.com>` |
| `ATTACHMENT_SCANNER`, `CLAMAV_HOST` | no | `clamav`, `clamav.opsdesk.local` |
| `AI_*` | key = yes | Section 14 |

---

## 7. Run migrations and create the first tenant

Run as one-off ECS tasks using the api image. Do this on every release, before updating services.

```bash
# Migrations + row-level security + grants (uses MIGRATION_DATABASE_URL)
aws ecs run-task --cluster opsdesk --launch-type FARGATE --task-definition opsdesk-api \
  --network-configuration "awsvpcConfiguration={subnets=[<private-a>,<private-b>],securityGroups=[<app-sg>]}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["migrate"]}]}'

# First tenant and its admin (once). One-off tasks can't prompt, so pass the password explicitly and change it after first sign-in.
aws ecs run-task ... --overrides '{"containerOverrides":[{"name":"api","command":
  ["create-tenant","--name","Your Co","--slug","yourco","--admin-email","you@yourco.com",
   "--admin-password","<from-secret>","--timezone","Asia/Kolkata"]}]}'
```

`create-tenant` installs the starter configuration: ITIL workflows for incident, request, problem and change, P1-P4 priorities and matrix, standard and premium SLA policies, a business-hours calendar, categories (including AI/ML), and canned responses. Sign in as the tenant admin and tailor it under Settings → Configuration. Tenants themselves are managed from the CLI (`opsdesk tenants list|set|setting`).

---

## 8. ECS services

Create four services (five with ClamAV) from two task definitions.

| Service | Image | Command | Desired | Health check |
|---|---|---|---|---|
| api | opsdesk-api | `api` | 2+ | ALB target group: `GET /healthz` on 8001 |
| worker | opsdesk-api | `worker` | 2+ | none (monitor queue depth) |
| beat | opsdesk-api | `beat` | **1** (min 1, max 1; deployment min healthy 0%, max 100%) | none |
| web | opsdesk-web | (default) | 2 | ALB target group: `GET /` on 8080 |
| clamav (optional) | `clamav/clamav:stable` | (default) | 1-2 | TCP 3310; give it 3 GB memory |

- Enable **ECS Exec** on api for break-glass `opsdesk` CLI commands.
- Set `stopTimeout` to 60 seconds on the worker so in-flight jobs finish. Tasks use `acks_late`, so an interrupted job is retried.
- Mount nothing. Containers are stateless; files live in S3.

---

## 9. Load balancer, WAF and DNS

**ALB listener rules (HTTPS 443):**

1. Path `/api/*`, `/healthz` → **api** target group (port 8001).
2. Default → **web** target group (port 8080).

Also add an HTTP 80 → HTTPS 301 redirect. Use TLS policy `ELBSecurityPolicy-TLS13-1-2-2021-06`. The ALB sends `X-Forwarded-Proto`, which uvicorn trusts (`--proxy-headers`) for HTTPS detection.

**AWS WAF web ACL on the ALB:**

| Rule | Purpose |
|---|---|
| AWSManagedRulesCommonRuleSet | OWASP-style protections. Exclude `SizeRestrictions_BODY` for `/api/tickets/*/attachments/` and `/api/channels/inbound/*`, which legitimately carry larger bodies |
| AWSManagedRulesKnownBadInputsRuleSet, AWSManagedRulesAmazonIpReputationList | Known exploits and bad IPs |
| Rate rule: `/api/auth/login/` | 100 requests per 5 minutes per IP (the app also throttles 10/min) |
| Rate rule: `/api/channels/inbound/*` | 3,000 requests per 5 minutes per IP (protects webhooks) |
| Rate rule: global | 5,000 requests per 5 minutes per IP |
| Optional geo rule on `/api/*` | Restrict to your office countries or VPN egress IPs |

**DNS:** create a Route 53 alias `desk.yourco.com` → ALB.

---

## 10. Outbound email (notifications and replies)

1. In SES, verify the domain `yourco.com` with **Easy DKIM** (three CNAMEs) and set a custom MAIL FROM (`bounce.yourco.com`).
2. Publish SPF (`v=spf1 include:amazonses.com ~all` on the MAIL FROM domain) and DMARC (`_dmarc.yourco.com TXT "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourco.com"`).
3. Request SES production access (leave the sandbox) and create SMTP credentials for `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD`.
4. Configure an SES configuration set that sends bounces and complaints to SNS. Suppress bad addresses so reputation stays healthy.

---

## 11. Inbound email: choose one

| Option | Best when | Setup |
|---|---|---|
| **A. IMAP channel** | Customers already email a Microsoft 365 / Google Workspace shared mailbox | Admin → Channels → kind `imap`, config `{"host":"outlook.office365.com","port":993,"username":"support@yourco.com","password":"<app password>","folder":"INBOX","from_address":"support@yourco.com"}`. Polled every 2 minutes. |
| **B. SendGrid Inbound Parse** | You want push delivery and use SendGrid | Point an MX for `inbound.yourco.com` at SendGrid. Set the parse URL to the channel's inbound URL plus `?token=<webhook_token>`. Channel kind `email`, config `{"provider":"sendgrid","webhook_token":"...","from_address":"support@yourco.com","reply_to":"support@inbound.yourco.com"}`. |
| **C. SES receiving** | All-AWS | SES receipt rule → S3 → Lambda (below) → channel kind `email` with `"provider":"generic_json"`. |

Lambda for option C (Python 3.12, needs network access to the ALB):

```python
import base64, email, json, os, urllib.request, boto3
from email import policy

s3 = boto3.client("s3")

def handler(event, _):
    rec = event["Records"][0]["ses"]["mail"]
    obj = s3.get_object(Bucket=os.environ["BUCKET"], Key=os.environ["PREFIX"] + rec["messageId"])
    msg = email.message_from_bytes(obj["Body"].read(), policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    payload = {
        "from": msg["From"], "subject": msg["Subject"], "message_id": msg["Message-ID"],
        "in_reply_to": msg.get("In-Reply-To", ""), "references": msg.get("References", ""),
        "text": body.get_content() if body else "",
        "attachments": [{"filename": p.get_filename(), "content_type": p.get_content_type(),
                         "content_base64": base64.b64encode(p.get_payload(decode=True) or b"").decode()}
                        for p in msg.iter_attachments()],
    }
    req = urllib.request.Request(os.environ["INBOUND_URL"], data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20)
```

Set `INBOUND_URL` to `https://desk.yourco.com/api/channels/inbound/<token>/?token=<webhook_token>`.

Customers' replies thread automatically. The system matches the `[INC-000123]` subject tag or the email threading headers, maps senders to accounts by `email_domains`, and blocks senders from other accounts from posting on a ticket.

---

## 12. WhatsApp Business (Meta Cloud API)

1. In **Meta Business Manager**, verify your business and create a **WhatsApp Business Account**. Add and verify the phone number you will use. It must not be active in the WhatsApp consumer app.
2. In **Meta for Developers**, create an app of type *Business* and add the **WhatsApp** product. Note the **Phone number ID** and the **App secret** (App settings → Basic).
3. Create a **System User** in Business Settings, assign the app and WhatsApp account, and generate a **permanent token** with `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
4. In OpsDesk admin → Channels, add kind `whatsapp`:

   ```json
   config:   {"phone_number_id": "1234567890", "access_token": "EAAG...", "app_secret": "abcd...",
              "verify_token": "<any random string>", "api_version": "v21.0",
              "template_name": "ticket_update", "template_language": "en"}
   settings: {"ticket_type": "incident", "team": "Service desk L1", "thread_window_hours": 72,
              "account_code": "<optional default account>"}
   ```

   Save the channel and copy the generated inbound URL.
5. In the Meta app go to **WhatsApp → Configuration → Webhook**. Enter the callback URL (the inbound URL) and the same verify token, then **subscribe to `messages`**. Meta calls the URL with a GET handshake; OpsDesk answers it.
6. Create and get approval for a **message template** named `ticket_update`, category *Utility*, body `Update on your ticket {{1}}: {{2}}`. Replies sent more than 24 hours after the customer's last message use this template, as Meta policy requires.
7. **Opt-in and records:** keep evidence that customers opted in to WhatsApp updates (Meta policy and DPDP consent). Map known users' phone numbers in E.164 format on their user record so messages land on the right account.

---

## 13. Monitoring and alerting

| Signal | How | Alarm |
|---|---|---|
| API health | ALB target health, `/healthz` | Unhealthy targets > 0 for 5 min |
| Errors | ALB 5xx count, app logs (JSON to stdout) | 5xx > 1% for 5 min |
| Latency | ALB `TargetResponseTime` p95 | > 1.5 s for 10 min |
| Background backlog | Redis `LLEN celery` (CloudWatch agent or a small Lambda) | > 200 for 10 min |
| Scheduler alive | beat task running count | < 1 |
| Inbound failures | `InboundMessage.status = failed` (admin list) or a log metric filter on `inbound processing failed` | > 0 in 15 min |
| Email | SES bounce/complaint rate | bounce > 4%, complaint > 0.08% |
| Database | CPU, FreeStorageSpace, DatabaseConnections, replica lag | CPU > 75%, storage < 20% |

Point OpsDesk's own Prometheus or CloudWatch alerts at its webhook channel, so platform incidents become OpsDesk tickets.

---

## 14. AI providers

| Mode | Settings | Notes |
|---|---|---|
| Offline (default) | `AI_EMBEDDING_PROVIDER=hashing`, `AI_LLM_PROVIDER=none` | No data leaves your VPC; similar tickets, duplicates, triage vote, deflection |
| Neural embeddings | `AI_EMBEDDING_PROVIDER=openai`, `AI_EMBEDDING_BASE_URL`, `AI_EMBEDDING_MODEL`, `AI_EMBEDDING_API_KEY` | Any OpenAI-compatible endpoint supporting `dimensions=384`. Run `manage.py reindex_embeddings` after switching |
| Claude for summaries, drafts, triage | `AI_LLM_PROVIDER=anthropic`, `AI_LLM_MODEL`, `AI_LLM_API_KEY` | Use a fast model for cost. Check model availability for your region and account |
| Self-hosted / gateway | `AI_LLM_PROVIDER=openai`, `AI_LLM_BASE_URL=https://<your-gateway>/v1` | vLLM, Ollama, LiteLLM, Azure OpenAI v1 endpoint |

`AI_REDACT_PII=1` (the default) masks personal data and secrets before every external call. For customers with strict data-processing terms, keep the offline mode, or route through a gateway in your own region. Review the dashboard's "AI suggestions accepted" rate before enabling `auto_apply_triage` on a tenant.

---

## 15. Operations

**Release procedure:**

1. Build and push images tagged with the commit SHA.
2. Run the `migrate` task and wait for it to succeed.
3. Update the api, worker and web services (rolling, minimum 100% healthy).
4. Update beat (stop-then-start).

Migrations are additive by convention. Split destructive changes across two releases.

**Backup and DR:**

| Asset | Mechanism | Target |
|---|---|---|
| PostgreSQL | RDS automated backups with PITR, plus a daily snapshot copied to the DR region | RPO ≤ 5 min, RTO ≤ 1 h |
| Attachments | S3 versioning, optional cross-region replication | RPO ≤ 15 min |
| Config and secrets | Secrets Manager replication, IaC in git | n/a |

Run a restore drill each quarter: restore to a new instance, point a staging stack at it, and run the smoke test below.

**Key rotation:**

- **`FIELD_ENCRYPTION_KEYS`**: prepend a new key, deploy, then re-encrypt every channel with the new key and remove the old one afterwards:

  ```bash
  opsdesk reencrypt-channels
  ```
- **Database passwords**: rotate in Secrets Manager, then redeploy.
- **`JWT_SECRET_KEY`**: rotating it ends all sessions.

**Tenant offboarding:** export data first through the API or `pg_dump --where`. Deleting a tenant cascades, but the audit log is append-only by design, so a DBA must temporarily disable the `audit_immutable` trigger, inside a change ticket.

**Smoke test after every deploy:**

1. `GET /healthz` returns `{"status":"ok"}`.
2. Sign in as an agent and as a customer. Confirm the customer sees only their own account's tickets.
3. Raise a ticket in the portal. It is numbered, gets SLA due dates and auto-assigns, and the requester receives an acknowledgement email.
4. Send an email to the support address. It becomes a ticket; an agent reply reaches the mailbox with the `[INC-…]` subject.
5. Send a WhatsApp message. It becomes a ticket, and an agent reply arrives on the phone.
6. Post a test alert twice. You get one ticket with two occurrences.
7. Check that the "Similar tickets" panel populates.

---

## 16. Security and compliance mapping

| Control area | ISO/IEC 27001:2022 Annex A | OWASP ASVS 4.0 | How OpsDesk meets it |
|---|---|---|---|
| Access control and least privilege | A.5.15, A.5.18, A.8.2, A.8.3 | V4 | Role-based permissions; account scoping; PostgreSQL RLS with a non-owner app role; owner credentials only in the migrate task |
| Authentication | A.5.17, A.8.5 | V2, V3 | 12-character minimum with common-password check, TOTP MFA, 15-minute JWTs, rotating refresh tokens with blacklist, throttled login |
| Cryptography | A.8.24 | V6, V9 | TLS 1.2+ at ALB, `force_ssl` on RDS, Redis TLS, KMS at rest (RDS, S3), Fernet-encrypted channel credentials with rotation |
| Logging and monitoring | A.8.15, A.8.16 | V7 | Append-only audit log (DB trigger), full ticket event history, JSON logs to CloudWatch, alarms in section 13 |
| Malware protection | A.8.7 | V12 | Extension denylist, size limits, ClamAV INSTREAM scanning, downloads blocked until clean |
| Data leakage prevention | A.8.12 | V8 | PII and secret redaction before LLM calls; internal notes hidden from customers; private bucket with 5-minute presigned URLs |
| Secure development | A.8.25, A.8.28, A.8.29 | V1, V14 | Automated tests on SLA, workflow, channel security and data isolation; pinned dependencies; ECR image scanning |
| Network security | A.8.20, A.8.22 | V13 | Private subnets, security groups, WAF with managed rules and rate limits, signed webhooks |
| Backup and continuity | A.8.13, A.5.30 | n/a | PITR, cross-region snapshots, quarterly restore drill |
| Cloud services | A.5.23 | n/a | Managed services with encryption; region pinned for residency |
| Supplier relationships (AI providers) | A.5.19-A.5.22 | n/a | Offline AI by default; provider switch via configuration; redaction; review provider DPAs |
| Privacy (India DPDP Act 2023, GDPR) | A.5.34 | n/a | Region pinning, data minimisation in AI calls, WhatsApp opt-in evidence, documented retention. Add a retention job if your contracts specify deletion periods |

**Before go-live:** run an external penetration test of the internet-facing endpoints, turn on GuardDuty and Security Hub, and require MFA for all admin and manager accounts.

---

## 17. CI pipeline (GitHub Actions example)

```yaml
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_PASSWORD: postgres }
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 5s --health-retries 10
    env:
      DATABASE_URL: postgres://postgres:postgres@localhost:5432/opsdesk
      OPSDESK_DEBUG: "1"
      USE_LOCAL_CACHE: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r backend/requirements.txt
      - run: psql postgres://postgres:postgres@localhost:5432/postgres -c "CREATE DATABASE opsdesk"
      - run: psql postgres://postgres:postgres@localhost:5432/template1 -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"
      - run: cd backend && PYTHONPATH=$PWD python -m pytest   # needs the postgres service above (superuser URL via OPSDESK_TEST_ADMIN_URL) and a Redis service (OPSDESK_TEST_REDIS_URL)
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - run: npm ci && npm run build
```

Add a deploy job that builds and pushes to ECR, runs the migrate task and updates services, gated on the `main` branch and an environment approval.
