"""KB, AI, notifications, reports, CSAT, service catalog, approvals, saved queues and health."""
import json

import pytest
from sqlalchemy import select

from opsdesk.db.models import (Approval, CSATSurvey, KnowledgeArticle, Notification, NotificationPreference, Ticket, User)
from opsdesk.db.session import tenant_session
from tests.conftest import assign_to_employee, create_ticket_api

pytestmark = pytest.mark.usefixtures("wide_admin_access")


def resolve(api, h, t, notes="Restarted the service"):
    d = api.get(f"/api/tickets/{t['id']}/", headers=h).json()
    tr = next(x for x in d["transitions"] if x["name"] == "Resolve")
    r = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": tr["id"], "fields": {"resolution_code": "fixed", "resolution_notes": notes}})
    assert r.status_code == 200, r.text
    return r.json()


class TestHealth:
    def test_healthz_reports_database(self, api):
        assert api.get("/healthz").json() == {"status": "ok"}
        assert api.get("/api/healthz").status_code == 200

    def test_security_headers_and_json_errors(self, api):
        r = api.get("/api/tickets/")
        assert r.status_code == 401 and r.json()["detail"] and r.headers["x-content-type-options"] == "nosniff"
        assert api.get("/api/definitely-missing/").status_code == 404


class TestKnowledgeBase:
    def article(self, api, h, **kw):
        body = {"title": "Reset VPN", "body": "Open the client and click reset", "visibility": "public", "status": "published", "tags": ["vpn"], **kw}
        r = api.post("/api/kb/articles/", headers=h, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    def test_staff_crud_and_view_counter(self, api, auth):
        h = auth("employee")
        a = self.article(api, h)
        assert a["author"] and a["views"] == 0
        assert api.get(f"/api/kb/articles/{a['id']}/", headers=h).json()["views"] == 1
        assert api.patch(f"/api/kb/articles/{a['id']}/", headers=h, json={"title": "Reset the VPN", "tags": ["vpn", "net"]}).json()["title"] == "Reset the VPN"
        assert api.delete(f"/api/kb/articles/{a['id']}/", headers=h).status_code == 204
        assert api.get(f"/api/kb/articles/{a['id']}/", headers=h).status_code == 404

    def test_requesters_only_read_published_public_shared_or_own_account(self, api, world, auth):
        h, hc = auth("employee"), auth("customer")
        public = self.article(api, h, title="Public")
        draft = self.article(api, h, title="Draft", status="draft")
        internal = self.article(api, h, title="Internal", visibility="internal")
        other_acct = self.article(api, h, title="Other account")
        with tenant_session(world.tenant_id) as s:
            from opsdesk.db.models import Account
            acc = Account(tenant_id=world.tenant_id, name="Initech", code="INIT", notes="")
            s.add(acc)
            s.flush()
            s.get(KnowledgeArticle, other_acct["id"]).account_id = acc.id
        titles = {a["title"] for a in api.get("/api/kb/articles/", headers=hc).json()}
        assert titles == {"Public"}
        for hidden in (draft, internal, other_acct):
            assert api.get(f"/api/kb/articles/{hidden['id']}/", headers=hc).status_code == 404
            assert api.post(f"/api/kb/articles/{hidden['id']}/vote/", headers=hc, json={"helpful": True}).status_code == 404
        assert api.post(f"/api/kb/articles/{public['id']}/vote/", headers=hc, json={"helpful": False}).json() == {"ok": True}
        staff_view = api.get(f"/api/kb/articles/{public['id']}/", headers=h).json()
        assert staff_view["unhelpful_votes"] == 1

    def test_requesters_cannot_write(self, api, auth):
        hc = auth("customer")
        assert api.post("/api/kb/articles/", headers=hc, json={"title": "x", "body": "y"}).status_code == 403
        a = self.article(api, auth("employee"))
        assert api.patch(f"/api/kb/articles/{a['id']}/", headers=hc, json={"title": "pwn"}).status_code == 403
        assert api.delete(f"/api/kb/articles/{a['id']}/", headers=hc).status_code == 403

    def test_search_title_body_and_tag(self, api, auth):
        h = auth("employee")
        self.article(api, h, title="Printer guide", body="toner", tags=["hardware"])
        self.article(api, h, title="Other", body="nothing", tags=[])
        assert [a["title"] for a in api.get("/api/kb/articles/?q=PRINTER", headers=h).json()] == ["Printer guide"]
        assert [a["title"] for a in api.get("/api/kb/articles/?q=toner", headers=h).json()] == ["Printer guide"]
        assert [a["title"] for a in api.get("/api/kb/articles/?q=hardware", headers=h).json()] == ["Printer guide"]

    def test_article_from_resolved_ticket_is_a_draft_and_idempotent(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "Wi-Fi drops in meeting room")
        assert api.post("/api/kb/articles/from_ticket/", headers=h, json={"ticket_id": t["id"]}).status_code == 400  # not resolved yet
        resolve(api, h, t, "Replaced the access point")
        r = api.post("/api/kb/articles/from_ticket/", headers=h, json={"ticket_id": t["id"]})
        assert r.status_code == 201 and r.json()["status"] == "draft" and r.json()["generation_method"] == "template"
        assert "Replaced the access point" in r.json()["body"] and r.json()["source_ticket"] == t["id"]
        again = api.post("/api/kb/articles/from_ticket/", headers=h, json={"ticket_id": t["id"]})
        assert again.json()["id"] == r.json()["id"] and again.json()["duplicate"] is True
        assert api.post("/api/kb/articles/from_ticket/", headers=auth("customer"), json={"ticket_id": t["id"]}).status_code == 403

    def test_articles_get_embedded_so_search_can_find_them(self, api, world, auth):
        a = self.article(api, auth("employee"), title="Fix printer spooler crash", body="Restart the print spooler service")
        with tenant_session(world.tenant_id) as s:
            assert s.get(KnowledgeArticle, a["id"]).embedding is not None


class TestAi:
    def test_similar_tickets_prefers_the_closest_resolved_one(self, api, auth):
        h = auth("employee")
        old = create_ticket_api(api, h, "Checkout page returns 502 bad gateway", description="Customers see 502 at payment step after deploy")
        resolve(api, h, old, "Ingress pods were OOM killed; raised memory")
        create_ticket_api(api, h, "Cafeteria menu typo", description="The lunch menu has a spelling mistake")
        new = create_ticket_api(api, h, "Checkout returning 502 error", description="Payment step shows 502 bad gateway for customers")
        hits = api.get(f"/api/tickets/{new['id']}/similar/?resolved=1", headers=h).json()
        assert hits and hits[0]["id"] == old["id"] and hits[0]["match"] in ("Very close", "Strong")
        assert "memory" in hits[0]["resolution_notes"]
        assert all(x["subject"] != "Cafeteria menu typo" for x in hits[:1])

    def test_enrichment_flags_possible_duplicates_and_records_triage(self, api, auth):
        h = auth("employee")
        first = create_ticket_api(api, h, "Payments API timing out for merchants", description="authorize endpoint times out")
        second = create_ticket_api(api, h, "Payments API timing out for merchants again", description="authorize endpoint times out for merchants")
        d = api.get(f"/api/tickets/{second['id']}/", headers=h).json()
        dups = (d["ai"] or {}).get("triage", {}).get("possible_duplicates", [])
        assert any(x["number"] == first["number"] for x in dups)

    def test_deflection_search_for_requesters_respects_visibility(self, api, world, auth):
        ha, hc = auth("employee"), auth("customer")
        secret = create_ticket_api(api, ha, "VPN gateway certificate expired on the finance cluster", description="internal only")
        resolve(api, ha, secret, "Renewed the certificate")
        mine = create_ticket_api(api, hc, "VPN gateway certificate problem", description="vpn gateway certificate expired")
        assign_to_employee(api, world, auth, mine)
        resolve(api, ha, mine, "Renewed the certificate")
        api.post("/api/kb/articles/", headers=ha, json={"title": "Renew VPN certificate", "body": "Renew the vpn gateway certificate in the console",
                                                         "visibility": "public", "status": "published"})
        api.post("/api/kb/articles/", headers=ha, json={"title": "Internal VPN runbook", "body": "vpn gateway certificate internal steps",
                                                         "visibility": "internal", "status": "published"})
        r = api.post("/api/ai/search/", headers=hc, json={"text": "vpn gateway certificate expired"}).json()
        assert {t["id"] for t in r["tickets"]} == {mine["id"]}
        assert all(a["visibility"] == "public" for a in r["articles"]) and r["articles"]
        assert api.post("/api/ai/search/", headers=hc, json={"text": "short"}).json() == {"tickets": [], "articles": []}

    def test_kb_suggestions_for_a_ticket(self, api, auth):
        ha, hc = auth("employee"), auth("customer")
        api.post("/api/kb/articles/", headers=ha, json={"title": "Fix email sync on phones", "body": "email sync phone mailbox fix", "visibility": "public", "status": "published"})
        t = create_ticket_api(api, hc, "Email sync broken on my phone", description="mailbox does not sync on the phone")
        got = api.get(f"/api/tickets/{t['id']}/kb_suggestions/", headers=hc).json()
        assert [a["title"] for a in got] == ["Fix email sync on phones"]
        other = create_ticket_api(api, ha, "Agent only ticket")
        assert api.get(f"/api/tickets/{other['id']}/kb_suggestions/", headers=hc).status_code == 404

    def test_summary_draft_triage_and_apply(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "Disk almost full on build server", description="df shows 98 percent")
        s = api.post(f"/api/tickets/{t['id']}/ai_summary/", headers=h).json()["summary"]
        assert "Disk almost full" in s or "df shows" in s
        d = api.post(f"/api/tickets/{t['id']}/ai_draft/", headers=h).json()
        assert d["draft"].startswith("Hi") and isinstance(d["sources"], list)
        tr = api.post(f"/api/tickets/{t['id']}/ai_triage/", headers=h).json()
        assert "possible_duplicates" in tr
        with tenant_session(world.tenant_id) as s2:
            s2.get(Ticket, t["id"]).ai_triage = {"impact": 2, "urgency": 2, "basis": "similar_tickets", "confidence": 0.9}
        applied = api.post(f"/api/tickets/{t['id']}/apply_triage/", headers=h).json()
        assert applied["impact"] == 2 and applied["urgency"] == 2 and applied["priority"]["key"] == "P2"
        for ep in ("ai_summary", "ai_draft", "ai_triage", "apply_triage"):
            assert api.post(f"/api/tickets/{t['id']}/{ep}/", headers=auth("customer")).status_code == 403

    def test_feedback_is_recorded_and_feeds_acceptance_rate(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        for ok in (True, True, False):
            assert api.post("/api/ai/feedback/", headers=h, json={"ticket_id": t["id"], "feature": "similar", "accepted": ok}).status_code == 201
        assert api.get("/api/reports/overview/", headers=h).json()["ai_acceptance_pct"] == 66.7

    def test_redaction_masks_secrets_before_text_leaves(self):
        from opsdesk.ai.redaction import redact
        out = redact("mail bob@corp.com password: hunter2 key sk-abcdefghijklmnopqrstu ip 10.0.0.12 card 4111 1111 1111 1111 pan ABCDE1234F")
        assert all(x not in out for x in ("bob@corp.com", "hunter2", "sk-abcdef", "10.0.0.12", "4111", "ABCDE1234F"))

    def test_hashing_embedder_is_deterministic_normalised_384d(self):
        from opsdesk.ai.providers import HashingEmbedder
        a, b = HashingEmbedder().embed(["disk is full on server", "disk is full on server"])
        assert a == b and len(a) == 384 and abs(sum(x * x for x in a) - 1) < 1e-9
        c = HashingEmbedder().embed(["totally different words about lunch"])[0]
        assert sum(x * y for x, y in zip(a, c)) < 0.5


class TestNotifications:
    def test_assignment_creates_in_app_notification_and_read_state(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, auth("customer"), "Needs a fix")
        api.patch(f"/api/tickets/{t['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        n = api.get("/api/notifications/", headers=h).json()
        assert n["unread_count"] == 1 and n["results"][0]["event_type"] == "assigned" and n["results"][0]["ticket_number"] == t["number"]
        assert api.get("/api/notifications/unread_count/", headers=h).json() == {"unread_count": 1}
        nid = n["results"][0]["id"]
        assert api.post(f"/api/notifications/{nid}/read/", headers=h).json() == {"id": nid, "is_read": True}
        assert api.post(f"/api/notifications/{nid}/read/", headers=h).status_code == 200  # idempotent
        assert api.get("/api/notifications/unread_count/", headers=h).json()["unread_count"] == 0

    def test_notifications_are_private_to_their_recipient(self, api, world, auth):
        t = create_ticket_api(api, auth("customer"), "x")
        api.patch(f"/api/tickets/{t['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        nid = api.get("/api/notifications/", headers=auth("employee")).json()["results"][0]["id"]
        assert api.post(f"/api/notifications/{nid}/read/", headers=auth("team_lead")).status_code == 404
        assert api.get("/api/notifications/", headers=auth("team_lead")).json()["results"] == []

    def test_mark_all_read(self, api, world, auth):
        for i in range(3):
            t = create_ticket_api(api, auth("customer"), f"t{i}")
            api.patch(f"/api/tickets/{t['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        h = auth("employee")
        assert api.post("/api/notifications/mark_all_read/", headers=h).json() == {"marked_read": 3}
        assert api.get("/api/notifications/unread_count/", headers=h).json()["unread_count"] == 0

    def test_preferences_defaults_validation_and_effect(self, api, world, auth):
        h = auth("employee")
        defaults = api.get("/api/notifications/preferences/", headers=h).json()
        assert all(defaults.values()) and len(defaults) == 6
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(NotificationPreference)).first() is None  # reading does not create a row
        assert api.patch("/api/notifications/preferences/", headers=h, json={"notify_on_assignment": False}).json()["notify_on_assignment"] is False
        assert api.patch("/api/notifications/preferences/", headers=h, json={"bogus": True}).status_code == 400
        assert api.patch("/api/notifications/preferences/", headers=h, json={"email_enabled": "no"}).status_code == 400
        assert api.patch("/api/notifications/preferences/", headers=h, json=[1]).status_code == 400
        t = create_ticket_api(api, auth("customer"), "x")
        api.patch(f"/api/tickets/{t['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        assert api.get("/api/notifications/", headers=h).json()["results"] == []  # opted out of assignment alerts

    def test_event_stream_replays_and_withholds_internal_notes_from_requesters(self, api, world, auth):
        t = create_ticket_api(api, auth("customer"), "Stream me")
        api.patch(f"/api/tickets/{t['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        r = api.get("/api/events/stream/?once=1&last_event_id=1", headers=auth("employee"))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert "event: connected" in r.text and "event: notification" in r.text and '"type": "assigned"' in r.text
        assert api.get("/api/events/stream/?once=1", headers={}).status_code == 401
        with tenant_session(world.tenant_id) as s:
            s.add(Notification(tenant_id=world.tenant_id, recipient_id=world.users["customer"], event_type="reply",
                               title=f"Internal note on {t['number']}", body="", ticket_id=t["id"]))
        assert "Internal note" not in api.get("/api/events/stream/?once=1&last_event_id=1", headers=auth("customer")).text

    def test_resolution_notifies_the_requester_in_app(self, api, world, auth):
        t = assign_to_employee(api, world, auth, create_ticket_api(api, auth("customer"), "Fix it"))
        resolve(api, auth("employee"), t)
        kinds = [n["event_type"] for n in api.get("/api/notifications/", headers=auth("customer")).json()["results"]]
        assert "resolved" in kinds


class TestReports:
    def test_overview_numbers(self, api, world, auth):
        h = auth("employee")
        a = create_ticket_api(api, h, "A", impact=1, urgency=1)
        b = create_ticket_api(api, h, "B")
        resolve(api, h, b)
        o = api.get("/api/reports/overview/", headers=h).json()
        assert (o["open"], o["created"], o["resolved"], o["unassigned"]) == (1, 2, 1, 1)
        assert o["by_priority"][0]["key"] == "P1" and o["sla_compliance_pct"] == 100.0 and o["mttr_hours"] is not None
        assert sum(x["count"] for x in o["aging"]) == 1 and len(o["series"]) == 30 and o["series"][-1]["created"] == 2
        assert o["by_source"] == [{"source": "portal", "count": 2}] and o["csat_responses"] == 0

    def test_staff_only_and_account_filter(self, api, world, auth):
        for role in ("customer",):
            assert api.get("/api/reports/overview/", headers=auth(role)).status_code == 403
        create_ticket_api(api, auth("customer"), "mine")
        h = auth("team_lead")
        assert api.get(f"/api/reports/overview/?account={world.account_id}", headers=h).json()["created"] == 1
        assert api.get("/api/reports/overview/?account=999999", headers=h).json()["created"] == 0
        assert api.get("/api/reports/overview/?account=abc", headers=h).status_code == 400
        assert api.get("/api/reports/overview/?days=0", headers=h).status_code == 422

    def test_slices(self, api, auth):
        h = auth("employee")
        create_ticket_api(api, h, "x")
        assert api.get("/api/reports/tickets/volume/", headers=h).json()["created"] == 1
        assert "by_state" in api.get("/api/reports/tickets/status-breakdown/", headers=h).json()
        assert "sla_compliance_pct" in api.get("/api/reports/tickets/sla-compliance/", headers=h).json()
        assert "csat_avg" in api.get("/api/reports/csat/summary/", headers=h).json()
        assert "by_team" in api.get("/api/reports/agents/performance/", headers=h).json()

    @pytest.mark.parametrize("kind", ["overview", "tickets", "incidents", "workload"])
    def test_csv_exports(self, api, auth, kind):
        h = auth("employee")
        t = create_ticket_api(api, h, "=HYPERLINK(\"http://evil\")")
        api.post(f"/api/tickets/{t['id']}/designate_major/", headers=auth("team_lead"), json={"is_major_incident": True})
        r = api.get(f"/api/reports/export/?report={kind}&format=csv", headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and f"opsdesk_{kind}_" in r.headers["content-disposition"]
        assert "=HYPERLINK" not in r.text.replace("'=HYPERLINK", "")  # formula injection neutralised

    def test_pdf_export_and_validation(self, api, auth):
        h = auth("employee")
        r = api.get("/api/reports/export/?report=tickets&format=pdf", headers=h)
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        assert api.get("/api/reports/export/?format=xml", headers=h).status_code == 400
        assert api.get("/api/reports/export/?report=secrets", headers=h).status_code == 400
        assert api.get("/api/reports/export/", headers=auth("customer")).status_code == 403

    def test_reports_respect_ticket_visibility(self, api, world, auth):
        mine = create_ticket_api(api, auth("team_lead"), "Assigned to the employee")
        create_ticket_api(api, auth("team_lead"), "Someone else's")
        r = api.patch(f"/api/tickets/{mine['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        assert r.status_code == 200, r.text
        assert api.get("/api/reports/overview/", headers=auth("employee")).json()["created"] == 1
        assert api.get("/api/reports/overview/", headers=auth("team_lead")).json()["created"] == 2


class TestCsat:
    def survey_token(self, world, ticket_id):
        with tenant_session(world.tenant_id) as s:
            return s.scalars(select(CSATSurvey).where(CSATSurvey.ticket_id == ticket_id)).one().token

    def test_survey_roundtrip_without_authentication(self, api, world, auth):
        t = assign_to_employee(api, world, auth, create_ticket_api(api, auth("customer"), "Rate me"))
        resolve(api, auth("employee"), t)
        tok = self.survey_token(world, t["id"])
        info = api.get(f"/api/csat/{tok}/").json()
        assert info == {"ticket": t["number"], "subject": "Rate me", "score": None, "responded": False}
        assert api.post(f"/api/csat/{tok}/", json={"score": 0}).status_code in (400, 422)
        assert api.post(f"/api/csat/{tok}/", json={"score": 6}).status_code in (400, 422)
        done = api.post(f"/api/csat/{tok}/", json={"score": 5, "comment": "great"})
        assert done.json()["responded"] is True and done.json()["score"] == 5
        assert api.get(f"/api/tickets/csat/{tok}/status/").json()["score"] == 5
        assert api.get("/api/reports/overview/", headers=auth("employee")).json()["csat_avg"] == 5.0

    def test_unknown_or_short_tokens_are_404_and_nothing_leaks(self, api):
        assert api.get("/api/csat/" + "z" * 40 + "/").status_code == 404
        assert api.get("/api/csat/short/").status_code == 422


class TestCatalogAndApprovals:
    def make_item(self, api, h, **kw):
        tt = next(t["id"] for t in api.get("/api/admin/config/ticket-types/", headers=h).json()["results"] if t["key"] == "service_request")
        r = api.post("/api/admin/config/catalog-items/", headers=h, json={
            "name": "New laptop", "ticket_type_id": tt, "requires_approval": True, "approval_type": "manager",
            "form_schema": [{"name": "model", "label": "Model", "type": "select", "options": ["Air", "Pro"], "required": True},
                            {"name": "qty", "label": "Quantity", "type": "number"}], **kw})
        assert r.status_code == 201, r.text
        return r.json()

    def test_request_with_approval_routes_to_the_manager_and_the_approver_sees_it(self, api, world, auth):
        item = self.make_item(api, auth("manager"))
        he = auth("employee")
        with tenant_session(world.tenant_id) as s:  # the requester reports to the team lead
            s.get(User, world.users["employee"]).manager_id = world.users["team_lead"]
        r = api.post(f"/api/catalog/{item['id']}/request/", headers=he, json={"fields": {"model": "Pro", "qty": "2"}, "additional_notes": "ASAP"})
        assert r.status_code == 201
        body = r.json()
        assert body["state_key"] == "awaiting_approval" and body["approver"]["id"] == world.users["team_lead"] and body["requires_approval"]
        pending = api.get("/api/approvals/", headers=auth("team_lead")).json()
        assert len(pending) == 1 and pending[0]["ticket"]["number"] == body["number"] and pending[0]["requester"]["email"] == world.email("employee")
        assert api.get("/api/approvals/", headers=he).json() == []
        decided = api.post(f"/api/tickets/{body['id']}/approvals/{pending[0]['id']}/decide/", headers=auth("team_lead"), json={"decision": "approved"})
        assert decided.status_code == 200 and api.get("/api/approvals/", headers=auth("team_lead")).json() == []
        t = api.get(f"/api/tickets/{body['id']}/", headers=he).json()
        assert "Approved - fulfil" in {x["name"] for x in api.get(f"/api/tickets/{body['id']}/", headers=auth("employee")).json()["transitions"]}
        assert "model: Pro" in t["description"] and "Quantity" not in t["description"] and "qty: 2" in t["description"]

    def test_form_validation_and_no_eligible_approver(self, api, world, auth):
        item = self.make_item(api, auth("manager"))
        he = auth("employee")
        bad = api.post(f"/api/catalog/{item['id']}/request/", headers=he, json={"fields": {"qty": "abc"}})
        assert bad.status_code == 400 and "model" in bad.json() and "qty" in bad.json()
        assert api.post(f"/api/catalog/{item['id']}/request/", headers=he, json={"fields": {"model": "Huge"}}).status_code == 400
        assert api.post(f"/api/catalog/{item['id']}/request/", headers=he, json={"fields": "nope"}).status_code in (400, 422)
        with tenant_session(world.tenant_id) as s:
            for u in s.scalars(select(User).where(User.role.in_(["manager", "team_lead"]))):
                u.is_active = False
        # no manager, no department head, no active admin or manager left
        ok_fields = {"model": "Air"}
        r = api.post(f"/api/catalog/{item['id']}/request/", headers=he, json={"fields": ok_fields})
        assert r.status_code in (400, 401)

    def test_without_approval_the_request_goes_straight_to_fulfilment_queue(self, api, world, auth):
        item = self.make_item(api, auth("manager"), name="Mouse", requires_approval=False, approval_type="none", form_schema=[])
        r = api.post(f"/api/catalog/{item['id']}/request/", headers=auth("employee"), json={})
        assert r.status_code == 201 and r.json()["state_key"] == "new" and r.json()["approver"] is None

    def test_catalog_is_closed_to_customers_and_hides_inactive_items(self, api, auth):
        item = self.make_item(api, auth("manager"))
        assert api.get("/api/catalog/", headers=auth("customer")).status_code == 403
        assert api.post(f"/api/catalog/{item['id']}/request/", headers=auth("customer"), json={}).status_code == 403
        api.patch(f"/api/admin/config/catalog-items/{item['id']}/", headers=auth("manager"), json={"is_active": False})
        assert api.get("/api/catalog/", headers=auth("employee")).json() == []
        assert api.get(f"/api/catalog/{item['id']}/", headers=auth("employee")).status_code == 404

    def test_catalog_is_tenant_scoped(self, api, world, other_world, auth):
        item = self.make_item(api, auth("manager"))
        assert api.get(f"/api/catalog/{item['id']}/", headers=auth("employee", other_world)).status_code == 404


class TestSavedQueues:
    def test_personal_and_shared_queues_with_permissions(self, api, world, auth):
        ha, hm = auth("employee"), auth("team_lead")
        personal = api.post("/api/saved-queues/", headers=ha, json={"name": "My open", "params": {"open": "1", "assignee": "me"}, "is_shared": True})
        assert personal.status_code == 201 and personal.json()["is_shared"] is False and personal.json()["owner"]["id"] == world.users["employee"]
        shared = api.post("/api/saved-queues/", headers=hm, json={"name": "SLA risk", "params": {"sla_status": "at_risk"}, "is_shared": True}).json()
        assert shared["is_shared"] is True
        assert {q["name"] for q in api.get("/api/saved-queues/", headers=ha).json()} == {"My open", "SLA risk"}
        assert [q["name"] for q in api.get("/api/saved-queues/", headers=hm).json()] == ["SLA risk"]
        assert api.patch(f"/api/saved-queues/{shared['id']}/", headers=ha, json={"name": "hack"}).status_code == 403
        assert api.delete(f"/api/saved-queues/{shared['id']}/", headers=ha).status_code == 403
        assert api.patch(f"/api/saved-queues/{personal.json()['id']}/", headers=ha, json={"is_shared": True}).status_code == 403
        assert api.patch(f"/api/saved-queues/{personal.json()['id']}/", headers=ha, json={"name": "Renamed"}).json()["name"] == "Renamed"
        assert api.delete(f"/api/saved-queues/{personal.json()['id']}/", headers=ha).status_code == 204

    @pytest.mark.parametrize("body", [{"name": "", "params": {}}, {"name": "x", "params": {"evil": 1}}, {"name": "x", "params": {"q": "a" * 201}},
                                      {"name": "x", "params": {"q": ["list"]}}, {"name": "x", "params": "str"}, {"name": "y" * 101, "params": {}}])
    def test_invalid_queues_are_refused(self, api, auth, body):
        assert api.post("/api/saved-queues/", headers=auth("employee"), json=body).status_code == 400

    def test_duplicate_names_for_the_same_owner_and_requesters_excluded(self, api, auth):
        h = auth("employee")
        assert api.post("/api/saved-queues/", headers=h, json={"name": "Q", "params": {}}).status_code == 201
        assert api.post("/api/saved-queues/", headers=h, json={"name": "Q", "params": {}}).status_code == 400
        assert api.get("/api/saved-queues/", headers=auth("customer")).status_code == 403

    def test_other_users_personal_queues_are_invisible(self, api, world, auth):
        q = api.post("/api/saved-queues/", headers=auth("employee"), json={"name": "Private", "params": {}}).json()
        assert api.get(f"/api/saved-queues/{q['id']}/", headers=auth("team_lead")).status_code == 404
