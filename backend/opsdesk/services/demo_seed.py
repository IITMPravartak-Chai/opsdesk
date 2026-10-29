"""Loads a realistic demo tenant: 3 customer accounts, teams, CMDB (incl. AI models), channels, automation rules, KB and
~25 tickets with history so the similar-ticket engine and dashboards have something to work with.

``seed_demo(reset=True)`` recreates the tenant; the reset briefly disables the audit-log trigger, so it needs the schema
owner credentials (``MIGRATION_DATABASE_URL``)."""
from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import delete, select

from ..db.base import utcnow
from ..db.models import (Account, CIDependency, Category, Channel, ConfigurationItem, KnowledgeArticle, Rule, Service,
                         ServiceCatalogItem, SLAPolicy, State, Team, TeamMember, Tenant, Ticket, TicketType, User,
                         Workflow)
from ..db.session import get_owner_engine, plain_session, tenant_session
from ..security.passwords import make_password
from . import sla
from . import tickets as services
from .bootstrap import install_defaults

PASSWORD = "Demo!Passw0rd2026"

ACCOUNTS = [("Globex Retail", "GLBX", "gold", ["globex.com"]),
            ("Initech Bank", "INTB", "platinum", ["initech.com"]),
            ("Umbrella Health", "UMBH", "silver", ["umbrella-health.com"])]

STAFF = [("superadmin@demo.opsdesk.dev", "Rohan", "Mehta", "super_admin", None),
         ("manager@demo.opsdesk.dev", "Anita", "Rao", "manager", None),
         ("teamlead@demo.opsdesk.dev", "Vikram", "Iyer", "team_lead", None),
         ("priya@demo.opsdesk.dev", "Priya", "Nair", "employee", "Service desk L1"),
         ("arjun@demo.opsdesk.dev", "Arjun", "Menon", "employee", "Service desk L1"),
         ("meera@demo.opsdesk.dev", "Meera", "Krishnan", "employee", "Cloud platform"),
         ("david@demo.opsdesk.dev", "David", "Chen", "employee", "AI/ML ops")]

CUSTOMERS = [("sam@globex.com", "Sam", "Porter", "GLBX", "+14155550101"),
             ("lena@initech.com", "Lena", "Fischer", "INTB", "+4915112345678"),
             ("ravi@umbrella-health.com", "Ravi", "Kumar", "UMBH", "+919840012345")]

CIS = [  # name, type, account, tech, version, team, attributes
    ("Fraud scoring model", "ai_model", "INTB", "XGBoost on SageMaker", "3.2", "AI/ML ops",
     {"provider": "in-house", "auc_baseline": 0.91, "drift_metric": "PSI", "drift_threshold": 0.2}),
    ("Customer support assistant (RAG)", "llm_app", "GLBX", "Claude + pgvector + FastAPI", "1.8", "AI/ML ops",
     {"model": "claude-sonnet", "guardrails": "PII filter, topic allow-list", "eval_suite": "weekly"}),
    ("Claims OCR pipeline", "data_pipeline", "UMBH", "Airflow + Textract", "2.4", "AI/ML ops", {}),
    ("Online store", "application", "GLBX", "Next.js + Node", "5.1", "Cloud platform", {}),
    ("Payments API", "integration", "INTB", "Java Spring on EKS", "7.0", "Cloud platform", {}),
    ("Core banking gateway", "application", "INTB", "Kong + Java", "4.3", "Cloud platform", {}),
    ("Patient portal", "application", "UMBH", "FastAPI + React", "3.0", "Cloud platform", {}),
    ("Data warehouse", "platform", "UMBH", "Snowflake", "", "Cloud platform", {}),
    ("Kubernetes cluster (prod)", "infrastructure", None, "EKS 1.30", "1.30", "Cloud platform", {}),
]
DEPENDS = [("Online store", "Kubernetes cluster (prod)"), ("Payments API", "Kubernetes cluster (prod)"),
           ("Fraud scoring model", "Payments API"), ("Customer support assistant (RAG)", "Online store"),
           ("Patient portal", "Data warehouse"), ("Claims OCR pipeline", "Data warehouse")]

# account, type, subject, description, ci, category, impact, urgency, days_ago, final_state, resolution, source
TICKETS = [
    ("GLBX", "incident", "Checkout page returns 502 Bad Gateway", "Customers can't complete checkout since 10:05. Error 502 from the store.", "Online store", "Outage", 1, 1, 28, "closed", "Ingress controller pods were OOMKilled after a traffic spike. Raised memory limits to 1Gi and added HPA on ingress.", "email"),
    ("GLBX", "incident", "Store checkout failing with 502 error for some users", "Intermittent 502 on /checkout for ~20% of requests.", "Online store", "Outage", 2, 1, 9, "resolved", "Same root cause as earlier ingress OOM: one ingress replica pinned to a small node. Rescheduled and set pod anti-affinity.", "whatsapp"),
    ("GLBX", "incident", "Support chatbot giving wrong refund policy", "The assistant tells customers refunds are 60 days; policy is 30 days.", "Customer support assistant (RAG)", "Hallucination / unsafe output", 2, 2, 21, "closed", "Stale policy document in the vector index. Re-ingested policy docs, added document versioning and an eval case for refund policy.", "portal"),
    ("GLBX", "incident", "Chatbot answering with outdated return window", "Assistant quotes old returns policy to shoppers.", "Customer support assistant (RAG)", "Hallucination / unsafe output", 2, 3, 3, None, "", "email"),
    ("GLBX", "incident", "Assistant responses very slow (15+ seconds)", "Chat responses take 15-20s since this morning.", "Customer support assistant (RAG)", "Inference latency / outage", 3, 2, 14, "closed", "Retriever top_k was set to 50 after a config change. Reverted to 8 and added p95 latency alert at 4s.", "portal"),
    ("GLBX", "service_request", "Read access to production logs for new developer", "Please give Tom (tom@globex.com) read-only access to prod logs in Grafana.", None, "New access", 4, 3, 12, "closed", "Added tom@globex.com to grafana-viewers group, access confirmed.", "portal"),
    ("GLBX", "incident", "Product images not loading on mobile", "Images broken on iOS app product pages.", "Online store", "Bug / defect", 3, 3, 6, "resolved", "CDN cache rule stripped the webp content-type header. Fixed the rule and purged cache.", "portal"),
    ("GLBX", "incident", "Password reset emails not arriving", "Shoppers report no password reset email.", "Online store", "Login / SSO", 3, 2, 1, None, "", "email"),
    ("INTB", "incident", "Fraud model false positives spiked overnight", "Fraud declines up 4x since 02:00, many legit card transactions blocked.", "Fraud scoring model", "Model drift / accuracy", 1, 1, 25, "closed", "Upstream merchant-category feature changed encoding after a payments release. Rolled back feature mapping, retrained threshold, added schema check on features.", "webhook"),
    ("INTB", "incident", "High decline rate - suspect fraud model drift", "Merchants complaining of declines. PSI on amount feature is 0.31.", "Fraud scoring model", "Model drift / accuracy", 2, 1, 2, None, "", "email"),
    ("INTB", "incident", "Payments API latency above 2s", "p95 latency 2.4s on /authorize since deployment 7.0.", "Payments API", "Slowness", 2, 1, 18, "closed", "Connection pool exhausted after deploy (pool size reset to default 10). Set to 50 and added saturation alert.", "webhook"),
    ("INTB", "incident", "Core banking gateway TLS certificate expiring", "Certificate expires in 5 days for gateway.initech.com.", "Core banking gateway", "Vulnerability", 3, 2, 16, "closed", "Renewed certificate via ACM and enabled auto-renew with 30-day alert.", "email"),
    ("INTB", "service_request", "VPN access for external auditor", "Auditor from KPMG needs VPN access for 2 weeks.", None, "New access", 4, 3, 10, "closed", "Temporary VPN account created with 14-day expiry and MFA enforced.", "portal"),
    ("INTB", "incident", "Cannot log in with SSO - SAML error", "Users get 'Invalid SAML response' when signing in to the gateway console.", "Core banking gateway", "Login / SSO", 2, 2, 5, "resolved", "IdP signing certificate rotated by customer. Uploaded new IdP metadata.", "portal"),
    ("INTB", "incident", "Payment authorisations timing out", "Timeouts on authorize endpoint for some merchants.", "Payments API", "Slowness", 2, 2, 0, None, "", "whatsapp"),
    ("INTB", "incident", "Authorize endpoint timeouts for several merchants", "Merchants report authorisation timeouts on the payments authorize API since this morning.", "Payments API", "Slowness", 2, 2, 0, None, "", "email"),
    ("UMBH", "incident", "Claims OCR job failed - Airflow DAG red", "Nightly claims_ocr DAG failed at extract step, 1,200 claims pending.", "Claims OCR pipeline", "Job failure", 2, 2, 20, "closed", "Textract throttling (ProvisionedThroughputExceeded). Added exponential backoff and requested quota increase.", "email"),
    ("UMBH", "incident", "OCR pipeline failing on scanned PDFs", "DAG fails on large scanned PDFs since yesterday.", "Claims OCR pipeline", "Job failure", 3, 2, 4, "resolved", "PDFs over 10 pages exceeded synchronous API limit. Switched to async Textract job for large files.", "portal"),
    ("UMBH", "incident", "Patient portal slow during morning peak", "Portal pages take 8s to load between 8-10am.", "Patient portal", "Slowness", 2, 2, 15, "closed", "Missing index on appointments.patient_id. Added index; p95 down to 600ms.", "portal"),
    ("UMBH", "incident", "Data warehouse dashboards showing yesterday's data", "Finance dashboards not refreshed today.", "Data warehouse", "Data quality", 3, 3, 8, "closed", "Snowflake task suspended after warehouse credit limit hit. Increased monitor quota and resumed task.", "email"),
    ("UMBH", "service_request", "New Snowflake role for analytics team", "Need a read-only role for 4 analysts on CLAIMS schema.", "Data warehouse", "New access", 4, 4, 7, "closed", "Created ANALYST_RO role with SELECT on CLAIMS, granted to 4 users.", "portal"),
    ("UMBH", "incident", "Patient portal login loop after password change", "After changing password users are redirected back to login.", "Patient portal", "Login / SSO", 3, 2, 2, None, "", "email"),
    ("UMBH", "incident", "Portal very slow loading appointments", "Appointments page takes ages to load.", "Patient portal", "Slowness", 3, 3, 0, None, "", "whatsapp"),
    (None, "incident", "Kubernetes node NotReady in prod cluster", "Alertmanager: node ip-10-0-3-21 NotReady for 10m.", "Kubernetes cluster (prod)", "Compute", 2, 1, 11, "closed", "Node disk pressure from container logs. Rotated logs, enabled log rotation daemonset.", "webhook"),
    ("GLBX", "problem", "Recurring ingress OOM under traffic spikes", "Two incidents in a month caused by ingress memory exhaustion.", "Online store", "Outage", 2, 3, 8, None, "", "portal"),
    ("INTB", "change", "Upgrade Payments API connection pool library", "Upgrade HikariCP and set pool sizing from config.", "Payments API", None, 3, 3, 3, None, "", "portal"),
]

ARTICLES = [
    ("Fixing 502 errors caused by ingress controller memory", "## Symptoms\n502 Bad Gateway on web apps behind NGINX ingress during traffic spikes.\n\n## Fix\n1. Check `kubectl get pods -n ingress-nginx` for OOMKilled restarts.\n2. Raise memory limit to at least 1Gi.\n3. Enable HPA on the ingress deployment and pod anti-affinity.", "internal"),
    ("How to reset your password", "Use **Forgot password** on the sign-in page. The email arrives within 5 minutes; check spam. Links expire after 30 minutes.", "public"),
    ("Responding to ML model drift alerts", "## Triage\n- Check PSI per feature; >0.2 is significant.\n- Compare upstream schema/encoding with the training feature store.\n\n## Mitigate\nRoll back the feature mapping or switch to the shadow model, then retrain threshold.", "internal"),
    ("RAG assistant gives outdated answers", "Usually a stale document in the vector index. Re-ingest the source, enable document versioning, and add an evaluation case so it can't regress.", "internal"),
    ("Requesting access to systems", "Raise a **Service request** with category *New access*, name the person and the system, and your manager will be asked to approve.", "public"),
    ("Airflow DAG failures from AWS throttling", "Throttling errors (ThrottlingException, ProvisionedThroughputExceeded) need retries with exponential backoff and a quota increase request.", "internal"),
]


def _user(s, tid, email, first, last, role, **extra) -> User:
    u = User(email=email.lower(), username=email.lower(), tenant_id=tid, role=role, first_name=first, last_name=last,
             password=make_password(PASSWORD), is_staff=role in ("super_admin", "manager", "team_lead"), is_active=True, **extra)
    s.add(u)
    s.flush()
    return u


def _reset(tenant_id: int) -> None:
    """Remove the demo tenant. The audit table is append-only; its trigger is disabled only for this operation."""
    owner = get_owner_engine()
    with owner.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE core_auditlog DISABLE TRIGGER audit_immutable")
    try:
        with tenant_session(tenant_id) as s:
            s.execute(delete(ServiceCatalogItem).where(ServiceCatalogItem.tenant_id == tenant_id))
            s.execute(delete(Ticket).where(Ticket.tenant_id == tenant_id))
            s.execute(delete(TicketType).where(TicketType.tenant_id == tenant_id))
            s.execute(delete(Workflow).where(Workflow.tenant_id == tenant_id))
        with owner.begin() as conn:
            conn.exec_driver_sql("DELETE FROM core_user WHERE tenant_id = %s", (tenant_id,))
            conn.exec_driver_sql("DELETE FROM core_tenant WHERE id = %s", (tenant_id,))
    finally:
        with owner.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE core_auditlog ENABLE TRIGGER audit_immutable")


def _new_tenant() -> int:
    with plain_session() as ps:
        tenant = Tenant(name="Northwind Managed Services", slug="demo", timezone="Asia/Kolkata", settings={})
        ps.add(tenant)
        ps.flush()
        return tenant.id


def seed_demo(reset: bool = False, log=print) -> bool:
    """Create the demo tenant. Returns False when it already exists and ``reset`` is not set."""
    random.seed(7)
    with plain_session() as ps:
        existing_id = ps.scalar(select(Tenant.id).where(Tenant.slug == "demo"))
    if existing_id is not None:
        if not reset:
            log("Demo tenant exists (use --reset to recreate)")
            return False
        _reset(existing_id)
    tenant_id = _new_tenant()
    with tenant_session(tenant_id) as s:
        _seed(s, s.get(Tenant, tenant_id))
    log(f"Demo ready. Sign in as manager@demo.opsdesk.dev / {PASSWORD}")
    return True


def _seed(s, tenant) -> None:
    from ..ai.engine import embed_article
    from ..worker.tasks import enrich_ticket

    tid = tenant.id
    install_defaults(s, tenant)
    premium = s.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id == tid, SLAPolicy.name.startswith("Premium"))).one()
    accounts = {}
    for name, code, tier, domains in ACCOUNTS:
        a = Account(tenant_id=tid, name=name, code=code, tier=tier, email_domains=domains, notes="",
                    sla_policy_id=premium.id if tier == "platinum" else None)
        s.add(a)
        s.flush()
        accounts[code] = a
    teams = {}
    for n, strat in (("Service desk L1", "round_robin"), ("Cloud platform", "least_loaded"), ("AI/ML ops", "round_robin")):
        t = Team(tenant_id=tid, name=n, assignment_strategy=strat, email="", rr_cursor=0)
        s.add(t)
        s.flush()
        teams[n] = t
    staff = {}
    for email, first, last, role, team in STAFF:
        u = _user(s, tid, email, first, last, role)
        staff[email] = u
        if team:
            s.add(TeamMember(team_id=teams[team].id, user_id=u.id))
    s.add(TeamMember(team_id=teams["Service desk L1"].id, user_id=staff["teamlead@demo.opsdesk.dev"].id))
    customers = {}
    for email, first, last, code, phone in CUSTOMERS:
        customers[code] = _user(s, tid, email, first, last, "customer", account_id=accounts[code].id, phone=phone)
    s.flush()

    cis = {}
    for name, ctype, code, tech, ver, team, attrs in CIS:
        svc_name = name.split(" (")[0] + " service"
        acct = accounts.get(code)
        svc = s.scalars(select(Service).where(Service.tenant_id == tid, Service.name == svc_name,
                                              Service.account_id == (acct.id if acct else None))).first()
        if svc is None:
            svc = Service(tenant_id=tid, name=svc_name, account_id=acct.id if acct else None,
                          owner_team_id=teams[team].id, description="", criticality=3, is_active=True)
            s.add(svc)
            s.flush()
        ci = ConfigurationItem(tenant_id=tid, name=name, ci_type=ctype, account_id=acct.id if acct else None,
                               service_id=svc.id, tech_stack=tech, version=ver, owner_team_id=teams[team].id,
                               attributes=attrs, environment="prod", status="active")
        s.add(ci)
        s.flush()
        cis[name] = ci
    for a, b in DEPENDS:
        s.add(CIDependency(from_configurationitem_id=cis[a].id, to_configurationitem_id=cis[b].id))

    ai_parent = s.scalars(select(Category).where(Category.tenant_id == tid, Category.name == "AI / ML",
                                                 Category.parent_id.is_(None))).first()
    if ai_parent:
        for child in s.scalars(select(Category).where(Category.tenant_id == tid, Category.parent_id == ai_parent.id)):
            child.default_team_id = teams["AI/ML ops"].id

    s.add_all([
        Channel(tenant_id=tid, kind="email", name="Support mailbox", inbound_token="demo-email-inbound-0001", is_active=True,
                config={"provider": "generic_json", "webhook_token": "demo-email-secret",
                        "from_address": "support@northwind.example"},
                settings={"ticket_type": "incident", "team": "Service desk L1"}),
        Channel(tenant_id=tid, kind="whatsapp", name="WhatsApp support", inbound_token="demo-whatsapp-inbound-0001", is_active=True,
                config={"phone_number_id": "REPLACE", "access_token": "REPLACE", "app_secret": "demo-wa-secret",
                        "verify_token": "demo-verify", "api_version": "v21.0", "template_name": "ticket_update",
                        "template_language": "en"},
                settings={"ticket_type": "incident", "team": "Service desk L1", "thread_window_hours": 72}),
        Channel(tenant_id=tid, kind="webhook", name="Prometheus Alertmanager", inbound_token="demo-alerts-inbound-0001", is_active=True,
                config={"auth": "bearer", "secret": "demo-alert-secret", "items_path": "alerts",
                        "map": {"subject": "annotations.summary", "body": "annotations.description",
                                "external_id": "fingerprint", "fingerprint": "fingerprint", "status": "status",
                                "severity": "labels.severity", "ci": "labels.ci", "account_code": "labels.customer",
                                "timestamp": "startsAt"},
                        "severity_map": {"critical": [1, 1], "warning": [3, 2], "info": [4, 4]},
                        "resolved_value": "resolved"},
                settings={"ticket_type": "incident", "team": "Cloud platform", "auto_resolve": False}),
    ])
    s.add_all([
        Rule(tenant_id=tid, name="AI systems go to AI/ML ops", event="ticket.created", order=1, is_active=True,
             conditions=[{"field": "ci.ci_type", "op": "in", "value": ["ai_model", "llm_app", "data_pipeline"]}],
             actions=[{"type": "assign_team", "value": "AI/ML ops"}, {"type": "add_tag", "value": "ai"}]),
        Rule(tenant_id=tid, name="Critical alerts become major incidents", event="ticket.created", order=2, is_active=True,
             conditions=[{"field": "source", "op": "eq", "value": "webhook"},
                         {"field": "priority.key", "op": "eq", "value": "P1"}],
             actions=[{"type": "set_major_incident", "value": True},
                      {"type": "notify", "to": ["teamlead@demo.opsdesk.dev", "team"],
                       "subject": "MAJOR INCIDENT {number}: {subject}", "body": "{url}"}]),
        Rule(tenant_id=tid, name="Platinum accounts: flag and route to L1 lead", event="ticket.created", order=3,
             is_active=True, conditions=[{"field": "account.tier", "op": "eq", "value": "platinum"}],
             actions=[{"type": "add_tag", "value": "platinum"}]),
        Rule(tenant_id=tid, name="Escalate SLA breaches to the service manager", event="sla.breached", order=4,
             is_active=True, conditions=[],
             actions=[{"type": "notify", "to": ["teamlead@demo.opsdesk.dev"], "subject": "SLA breached {number}",
                       "body": "{subject}\n{url}"}, {"type": "add_tag", "value": "escalated"}]),
    ])
    for title, body, vis in ARTICLES:
        art = KnowledgeArticle(tenant_id=tid, title=title, body=body, visibility=vis, status="published",
                               author_id=staff["priya@demo.opsdesk.dev"].id, tags=[])
        s.add(art)
        s.flush()
        embed_article(s, art)
    s.commit()

    types = {t.key: t for t in s.scalars(select(TicketType).where(TicketType.tenant_id == tid))}
    cats = {c.name: c for c in s.scalars(select(Category).where(Category.tenant_id == tid))}
    agents = [staff[e] for e in ("priya@demo.opsdesk.dev", "arjun@demo.opsdesk.dev", "meera@demo.opsdesk.dev",
                                 "david@demo.opsdesk.dev")]
    now = utcnow()
    for (code, tkey, subject, desc, ci, cat, imp, urg, days, final, resolution, source) in TICKETS:
        requester = customers.get(code)
        t = services.create_ticket(
            s, tenant_id=tid, ticket_type=types[tkey], subject=subject, description=desc, requester=requester,
            account=accounts.get(code), source=source, ci=cis.get(ci), category=cats.get(cat) if cat else None,
            impact=imp, urgency=urg, created_by=requester,
            custom_fields={"change_type": "Normal", "risk": "Medium"} if tkey == "change" else {})
        created = now - timedelta(days=days, hours=random.randint(1, 9), minutes=random.randint(0, 59))
        t.created_at = created
        s.flush()
        sla.apply_sla(s, t, recompute=True)
        s.flush()
        agent = t.assignee or random.choice(agents)
        if t.assignee is None:
            services.update_ticket(s, t, None, {"assignee": agent})
        wf_states = {st.key: st for st in s.scalars(select(State).where(State.workflow_id == t.state.workflow_id))}
        if tkey in ("incident", "service_request"):
            start = wf_states.get("in_progress") or wf_states.get("in_fulfilment")
            services.set_state(s, t, start, actor=agent)
            it = services.add_interaction(
                s, t, body=f"Hi {requester.first_name if requester else 'team'}, I'm on this now and will update you shortly.",
                author=agent, deliver=False)
            responded = created + timedelta(minutes=random.choice([3, 6, 9, 12, 14, 25, 40]))
            it.created_at = responded
            t.first_responded_at = responded
            t.response_breached = bool(t.response_due_at and responded > t.response_due_at)
            t.updated_at = responded
            s.flush()
        elif tkey == "problem":
            services.set_state(s, t, wf_states["investigating"], actor=agent)
        if final:
            t.resolution_code = "fixed"
            t.resolution_notes = resolution
            s.flush()
            res_key = "fulfilled" if tkey == "service_request" else "resolved"
            services.set_state(s, t, wf_states[res_key], actor=agent)
            window = (t.resolution_due_at - created) if t.resolution_due_at else timedelta(hours=8)
            resolved_at = created + window * random.choice([0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.4])
            breached = bool(t.resolution_due_at and resolved_at > t.resolution_due_at) or t.response_breached
            if final == "closed":
                services.set_state(s, t, wf_states["closed"], actor=None, via="auto-close")
            t.resolved_at = resolved_at
            t.resolution_breached = breached
            t.closed_at = resolved_at + timedelta(days=5) if final == "closed" else None
            t.sla_status = "breached" if breached else ("met" if t.sla_policy_id else "none")
            s.flush()
            if final == "closed":
                s.refresh(t)
                if t.csat is not None:
                    t.csat.score = random.choice([4, 5, 5, 5, 3])
                    t.csat.responded_at = resolved_at + timedelta(hours=6)
                    s.flush()
        else:
            t.sla_status = sla.evaluate(t)
            s.flush()
        ticket_id = t.id
        s.commit()  # make the ticket visible to the enrichment task, which uses its own session
        enrich_ticket(tid, ticket_id)
