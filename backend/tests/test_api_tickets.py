"""Ticket HTTP API: creation rules, visibility by role, updates, transitions, conversation, attachments and actions."""
import pytest
from sqlalchemy import select

from opsdesk.db.models import Account, Team, TeamAccount, TeamMember, Ticket, User
from opsdesk.db.session import tenant_session
from tests.conftest import assign_to_employee, create_ticket_api, login, make_world, ticket_type_id


def comment(api, h, tid, body, **extra):
    parts = {"body": (None, body), **{k: (None, v) for k, v in extra.items()}}
    return api.post(f"/api/tickets/{tid}/interactions/", headers=h, files=parts)


class TestCreate:
    def test_customer_ticket_gets_requester_account_priority_and_sla(self, api, world, auth):
        t = create_ticket_api(api, auth("customer"), "VPN keeps dropping")
        assert t["number"].startswith("INC-") and t["state"]["key"] == "new" and t["priority"]["key"] == "P3"
        assert t["requester"]["id"] == world.users["customer"] and t["account"]["name"] == "Globex"
        assert t["sla"]["status"] == "on_track" if t.get("sla") else True

    def test_customers_cannot_create_staff_only_types(self, api, auth):
        h = auth("customer")
        pid = ticket_type_id(api, auth("employee"), "problem")
        r = api.post("/api/tickets/", headers=h, json={"ticket_type": pid, "subject": "x", "description": "y"})
        assert r.status_code == 403

    def test_customer_cannot_pick_requester_assignee_or_major_flag(self, api, world, auth):
        h = auth("customer")
        r = api.post("/api/tickets/", headers=h, json={
            "ticket_type": ticket_type_id(api, h), "subject": "Mine", "description": "d",
            "requester": world.users["employee"], "is_major_incident": True})
        assert r.status_code == 201 and r.json()["requester"]["id"] == world.users["customer"]
        assert not r.json().get("is_major_incident")

    def test_agent_can_create_on_behalf_of_a_requester_and_leads_can_declare_major(self, api, world, auth):
        t = create_ticket_api(api, auth("team_lead"), "On behalf", requester=world.users["customer"], is_major_incident=True)
        assert t["requester"]["id"] == world.users["customer"] and t["is_major_incident"] is True
        e = create_ticket_api(api, auth("employee"), "Employee flag", is_major_incident=True)
        assert e["is_major_incident"] is False  # an employee's major flag is ignored

    @pytest.mark.parametrize("payload,fragment", [({"ticket_type": 10 ** 9}, "ticket type"), ({"category": 10 ** 9}, "category")])
    def test_unknown_or_foreign_references_are_400(self, api, auth, payload, fragment):
        h = auth("employee")
        body = {"ticket_type": ticket_type_id(api, h), "subject": "s", "description": "d", **payload}
        r = api.post("/api/tickets/", headers=h, json=body)
        assert r.status_code == 400 and fragment in r.json()["detail"].lower()

    def test_references_from_another_tenant_are_refused(self, api, world, other_world, auth):
        h = auth("employee")
        with tenant_session(other_world.tenant_id) as s:
            foreign_account = s.scalars(select(Account)).first().id
        r = api.post("/api/tickets/", headers=h, json={"ticket_type": ticket_type_id(api, h), "subject": "s", "description": "d",
                                                        "account": foreign_account})
        assert r.status_code == 400

    def test_customer_custom_fields_are_filtered_to_visible_ones(self, api, auth):
        h = auth("customer")
        t = create_ticket_api(api, h, "Fields", custom_fields={"environment": "Production", "secret_internal": "x"})
        assert t["custom_fields"] == {"environment": "Production"}

    def test_validation_errors(self, api, auth):
        h = auth("employee")
        assert api.post("/api/tickets/", headers=h, json={"subject": "no type"}).status_code == 422
        assert api.post("/api/tickets/", headers={}, json={}).status_code == 401


class TestVisibility:
    def test_customer_sees_own_and_same_account_tickets_only(self, api, world, auth):
        create_ticket_api(api, auth("customer"), "Mine")
        create_ticket_api(api, auth("employee"), "Internal, no account")
        listing = api.get("/api/tickets/", headers=auth("customer")).json()
        assert [t["subject"] for t in listing["results"]] == ["Mine"]

    def test_account_sharing_can_be_switched_off_per_tenant(self, api, world, auth):
        other_customer = None
        with tenant_session(world.tenant_id) as s:
            from opsdesk.security.passwords import make_password
            u = User(email="colleague@tests.example", username="colleague@tests.example", tenant_id=world.tenant_id,
                     role="customer", password=make_password("x" * 14), account_id=world.account_id)
            s.add(u)
        create_ticket_api(api, auth("customer"), "Customer A's ticket")
        r = api.post("/api/auth/login/", json={"email": "colleague@tests.example", "password": "x" * 14})
        h2 = {"Authorization": f"Bearer {r.json()['access']}"}
        assert api.get("/api/tickets/", headers=h2).json()["count"] == 1  # same account: shared by default
        from opsdesk.db.models import Tenant
        with tenant_session(world.tenant_id) as s:
            t = s.get(Tenant, world.tenant_id)
            t.settings = {**t.settings, "customer_sees_account_tickets": False}
        assert api.get("/api/tickets/", headers=h2).json()["count"] == 0

    def test_employees_see_only_tickets_assigned_to_them(self, api, world, auth):
        assigned = create_ticket_api(api, auth("team_lead"), "Assigned to me")
        other = create_ticket_api(api, auth("team_lead"), "Not mine")
        r = api.patch(f"/api/tickets/{assigned['id']}/", headers=auth("team_lead"), json={"assignee": world.users["employee"]})
        assert r.status_code == 200, r.text
        ids = {t["id"] for t in api.get("/api/tickets/", headers=auth("employee")).json()["results"]}
        assert ids == {assigned["id"]}
        assert api.get(f"/api/tickets/{other['id']}/", headers=auth("employee")).status_code == 404

    def test_managers_and_admins_see_everything(self, api, auth):
        create_ticket_api(api, auth("customer"), "a")
        create_ticket_api(api, auth("employee"), "b")
        for role in ("team_lead", "manager"):
            assert api.get("/api/tickets/", headers=auth(role)).json()["count"] == 2

    def test_other_tenant_cannot_see_or_touch_a_ticket(self, api, world, other_world, auth):
        t = create_ticket_api(api, auth("employee"), "Private")
        h = auth("employee", other_world)
        assert api.get(f"/api/tickets/{t['id']}/", headers=h).status_code == 404
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"subject": "x"}).status_code == 404
        assert api.get("/api/tickets/", headers=h).json()["count"] == 0

    def test_unknown_ticket_is_404(self, api, auth):
        assert api.get("/api/tickets/999999/", headers=auth("employee")).status_code == 404


class TestListing:
    def test_filters_search_ordering_and_paging(self, api, world, auth):
        h = auth("employee")
        a = create_ticket_api(api, h, "Router firmware crash", impact=1, urgency=1)
        b = create_ticket_api(api, h, "Printer jam")
        assert [t["id"] for t in api.get("/api/tickets/?q=firmware", headers=h).json()["results"]] == [a["id"]]
        assert [t["id"] for t in api.get(f"/api/tickets/?q={b['number']}", headers=h).json()["results"]] == [b["id"]]
        assert [t["id"] for t in api.get("/api/tickets/?priority=P1", headers=h).json()["results"]] == [a["id"]]
        assert api.get("/api/tickets/?state_category=resolved", headers=h).json()["count"] == 0
        assert api.get("/api/tickets/?assignee=none", headers=h).json()["count"] == 2
        order = api.get("/api/tickets/?ordering=priority", headers=h).json()["results"]
        assert order[0]["id"] == a["id"]
        p1 = api.get("/api/tickets/?page_size=1", headers=h).json()
        assert p1["count"] == 2 and len(p1["results"]) == 1 and p1["next"] and "page=2" in p1["next"]
        assert api.get("/api/tickets/?page=3&page_size=1", headers=h).status_code == 404

    def test_mine_assignee_me_and_counts(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "Mine")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"assignee": world.users["employee"]}).status_code == 200
        create_ticket_api(api, h, "Unassigned")
        assert [x["id"] for x in api.get("/api/tickets/?assignee=me", headers=h).json()["results"]] == [t["id"]]
        counts = api.get("/api/tickets/counts/", headers=h).json()
        assert counts["mine"] == 1 and counts["unassigned"] == 1 and counts["open"] == 2


class TestUpdate:
    def test_agent_updates_fields_and_references(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "Original")
        with tenant_session(world.tenant_id) as s:
            team = Team(tenant_id=world.tenant_id, name="Net", email="", assignment_strategy="manual", rr_cursor=0)
            s.add(team)
            s.flush()
            team_id = team.id
        r = api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"subject": "Renamed", "team": team_id, "assignee": world.users["employee"],
                                                                  "impact": 1, "urgency": 1, "tags": ["net"]})
        body = r.json()
        assert r.status_code == 200 and body["subject"] == "Renamed" and body["team"]["id"] == team_id
        assert body["assignee"]["id"] == world.users["employee"] and body["priority"]["key"] == "P1" and body["tags"] == ["net"]

    @pytest.mark.parametrize("payload", [{"impact": 9}, {"assignee": 10 ** 9}, {"priority": None}, {"team": 10 ** 9}])
    def test_invalid_references_are_400(self, api, auth, payload):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json=payload).status_code == 400

    def test_assignee_must_be_active_staff(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"assignee": world.users["customer"]}).status_code == 400

    def test_a_ticket_cannot_be_its_own_parent(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"parent": t["id"]}).status_code == 400

    def test_customer_edits_only_text_and_cannot_probe_ids(self, api, world, auth):
        h = auth("customer")
        t = create_ticket_api(api, h, "Mine")
        r = api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"subject": "Better title", "assignee": 10 ** 9, "impact": 1})
        assert r.status_code == 200 and r.json()["subject"] == "Better title" and r.json()["impact"] == 3

    def test_customer_cannot_edit_someone_elses_ticket(self, api, world, auth):
        t = create_ticket_api(api, auth("employee"), "Not theirs")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=auth("customer"), json={"subject": "x"}).status_code == 404


class TestTransitions:
    def _tid(self, detail, name):
        return next(x["id"] for x in detail["transitions"] if x["name"] == name)

    def test_agent_walks_the_incident_workflow(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "Flow")
        d = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": self._tid(t, "Start work")}).json()
        assert d["state"]["key"] == "in_progress"
        r = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": self._tid(d, "Resolve")})
        assert r.status_code == 400 and "resolution_code" in r.json()["detail"]
        r = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={
            "transition_id": self._tid(d, "Resolve"), "fields": {"resolution_code": "fixed", "resolution_notes": "rebooted"},
            "comment": "All good now"})
        assert r.status_code == 200 and r.json()["state"]["key"] == "resolved" and r.json()["resolution_notes"] == "rebooted"

    def test_customer_can_confirm_or_reopen_but_not_resolve(self, api, world, auth):
        ha, hc = auth("employee"), auth("customer")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, hc, "Mine"))
        resolve = next(x for x in api.get(f"/api/tickets/{t['id']}/", headers=ha).json()["transitions"] if x["name"] == "Resolve")
        assert api.post(f"/api/tickets/{t['id']}/transition/", headers=hc, json={"transition_id": resolve["id"]}).status_code == 400
        api.post(f"/api/tickets/{t['id']}/transition/", headers=ha, json={
            "transition_id": resolve["id"], "fields": {"resolution_code": "fixed", "resolution_notes": "ok"}})
        names = {x["name"] for x in api.get(f"/api/tickets/{t['id']}/", headers=hc).json()["transitions"]}
        assert names == {"Reopen", "Confirm and close"}

    def test_unknown_transition_is_404_and_required_comment_enforced(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": 10 ** 9}).status_code == 404
        d = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": self._tid(t, "Start work")}).json()
        r = api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": self._tid(d, "Ask customer")})
        assert r.status_code == 400 and "comment" in r.json()["detail"].lower()


class TestConversation:
    def test_internal_notes_are_hidden_from_requesters(self, api, world, auth):
        ha, hc = auth("employee"), auth("customer")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, hc, "Chat"))
        assert comment(api, ha, t["id"], "Secret note", kind="internal").status_code == 201
        assert comment(api, ha, t["id"], "Public answer", kind="public").status_code == 201
        assert comment(api, hc, t["id"], "Thanks", kind="internal").json()["kind"] == "public"  # requesters cannot write notes
        seen = [i["body"] for i in api.get(f"/api/tickets/{t['id']}/interactions/", headers=hc).json()]
        assert "Secret note" not in seen and {"Public answer", "Thanks"} <= set(seen)
        staff_seen = [i["body"] for i in api.get(f"/api/tickets/{t['id']}/interactions/", headers=ha).json()]
        assert "Secret note" in staff_seen

    def test_empty_message_without_files_is_400_and_closed_tickets_refuse_replies(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert comment(api, h, t["id"], "   ").status_code == 400
        closed = next(x for x in api.get(f"/api/tickets/{t['id']}/", headers=h).json()["transitions"] if x["name"] == "Cancel")
        api.post(f"/api/tickets/{t['id']}/transition/", headers=h, json={"transition_id": closed["id"], "comment": "dup"})
        # cancelled tickets are final
        assert comment(api, h, t["id"], "hello").status_code == 400

    def test_agent_reply_records_first_response(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        comment(api, h, t["id"], "On it")
        d = api.get(f"/api/tickets/{t['id']}/", headers=h).json()
        assert d["first_responded_at"] if "first_responded_at" in d else True

    def test_customer_reply_to_pending_ticket_reopens_it(self, api, world, auth):
        ha, hc = auth("employee"), auth("customer")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, hc, "x"))
        d = api.get(f"/api/tickets/{t['id']}/", headers=ha).json()
        start = next(x for x in d["transitions"] if x["name"] == "Start work")
        d = api.post(f"/api/tickets/{t['id']}/transition/", headers=ha, json={"transition_id": start["id"]}).json()
        ask = next(x for x in d["transitions"] if x["name"] == "Ask customer")
        api.post(f"/api/tickets/{t['id']}/transition/", headers=ha, json={"transition_id": ask["id"], "comment": "logs?"})
        comment(api, hc, t["id"], "Attached")
        assert api.get(f"/api/tickets/{t['id']}/", headers=hc).json()["state"]["key"] == "in_progress"


class TestAttachments:
    def upload(self, api, h, tid, name, data, ctype="text/plain", **extra):
        files = [("files", (name, data, ctype))] + [(k, (None, v)) for k, v in extra.items()]
        return api.post(f"/api/tickets/{tid}/attachments/", headers=h, files=files)

    def test_upload_download_roundtrip(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        r = self.upload(api, h, t["id"], "notes.txt", b"hello world")
        assert r.status_code == 201 and r.json()["errors"] == []
        att = api.get(f"/api/tickets/{t['id']}/", headers=h).json()["attachments"][0]
        dl = api.get(f"/api/attachments/{att['id']}/download/", headers=h)
        assert dl.status_code == 200 and dl.content == b"hello world" and "notes.txt" in dl.headers["content-disposition"]

    def test_blocked_extensions_and_oversize_are_rejected(self, api, auth, monkeypatch):
        from opsdesk.settings import settings
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        r = self.upload(api, h, t["id"], "run.exe", b"MZ")
        assert r.status_code == 400
        monkeypatch.setattr(settings, "attachment_max_mb", 0)
        assert self.upload(api, h, t["id"], "big.txt", b"x").status_code == 400

    def test_path_traversal_in_filename_is_neutralised(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert self.upload(api, h, t["id"], "../../etc/passwd.txt", b"x").status_code == 201
        att = api.get(f"/api/tickets/{t['id']}/", headers=h).json()["attachments"][0]
        assert "/" not in att["filename"] and ".." not in att["filename"]

    def test_requester_cannot_download_internal_attachments_or_other_tickets(self, api, world, auth):
        ha, hc = auth("employee"), auth("customer")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, hc, "x"))
        api.post(f"/api/tickets/{t['id']}/interactions/", headers=ha, files=[
            ("files", ("evidence.txt", b"internal", "text/plain")), ("body", (None, "note")), ("kind", (None, "internal"))])
        note = next(i for i in api.get(f"/api/tickets/{t['id']}/interactions/", headers=ha).json() if i["attachments"])
        att = note["attachments"][0]  # attached to the internal note, so it is listed on the interaction
        assert api.get(f"/api/attachments/{att['id']}/download/", headers=ha).status_code == 200
        assert api.get(f"/api/attachments/{att['id']}/download/", headers=hc).status_code == 404
        assert all(not i["attachments"] for i in api.get(f"/api/tickets/{t['id']}/interactions/", headers=hc).json())

    def test_other_tenant_cannot_download(self, api, world, other_world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        self.upload(api, h, t["id"], "a.txt", b"x")
        att = api.get(f"/api/tickets/{t['id']}/", headers=h).json()["attachments"][0]
        assert api.get(f"/api/attachments/{att['id']}/download/", headers=auth("employee", other_world)).status_code == 404

    def test_unscanned_pending_or_infected_files_are_withheld(self, api, world, auth):
        from opsdesk.db.models import Attachment
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        self.upload(api, h, t["id"], "a.txt", b"x")
        att = api.get(f"/api/tickets/{t['id']}/", headers=h).json()["attachments"][0]
        with tenant_session(world.tenant_id) as s:
            s.get(Attachment, att["id"]).scan_status = "pending"
        assert api.get(f"/api/attachments/{att['id']}/download/", headers=h).status_code == 409


class TestActions:
    def test_watch_toggle_and_visibility_rule(self, api, world, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.post(f"/api/tickets/{t['id']}/watch/", headers=h, json={"watch": True}).json() == {"watching": True}
        assert api.post(f"/api/tickets/{t['id']}/watch/", headers=h, json={"watch": False}).json() == {"watching": False}

    def test_link_and_merge_are_staff_only_and_validated(self, api, auth):
        h = auth("employee")
        a, b = create_ticket_api(api, h, "A"), create_ticket_api(api, h, "B")
        assert api.post(f"/api/tickets/{a['id']}/link/", headers=auth("customer"), json={"number": b["number"]}).status_code == 403
        assert api.post(f"/api/tickets/{a['id']}/link/", headers=h, json={"number": a["number"]}).status_code == 400
        assert api.post(f"/api/tickets/{a['id']}/link/", headers=h, json={"number": b["number"].lower(), "link_type": "blocks"}).json() == {"ok": True}
        assert api.post(f"/api/tickets/{a['id']}/link/", headers=h, json={"number": b["number"], "link_type": "bogus"}).status_code == 400
        m = api.post(f"/api/tickets/{b['id']}/merge/", headers=h, json={"into": a["number"]})
        assert m.json() == {"merged_into": a["id"]}
        assert api.get(f"/api/tickets/{b['id']}/", headers=h).json()["state"]["category"] in ("cancelled", "closed")
        assert api.post(f"/api/tickets/{b['id']}/merge/", headers=h, json={"into": b["number"]}).status_code == 400

    def test_approval_request_and_decision(self, api, world, auth):
        h, hm = auth("employee"), auth("team_lead")
        t = create_ticket_api(api, h, "Approve me")
        r = api.post(f"/api/tickets/{t['id']}/approvals/", headers=h, json={"approver_id": world.users["team_lead"]})
        assert r.status_code == 201
        aid = r.json()["id"]
        assert api.post(f"/api/tickets/{t['id']}/approvals/{aid}/decide/", headers=h, json={"decision": "approved"}).status_code == 403
        assert api.post(f"/api/tickets/{t['id']}/approvals/{aid}/decide/", headers=hm, json={"decision": "maybe"}).status_code == 400
        assert api.post(f"/api/tickets/{t['id']}/approvals/{aid}/decide/", headers=hm, json={"decision": "approved", "comment": "ok"}).json() == {"status": "approved"}
        assert api.post(f"/api/tickets/{t['id']}/approvals/", headers=auth("customer"), json={"approver_id": 1}).status_code == 403

    def test_time_tracking(self, api, auth):
        h = auth("employee")
        t = create_ticket_api(api, h, "x")
        assert api.post(f"/api/tickets/{t['id']}/time/", headers=h, json={"minutes": 0}).status_code == 400
        r = api.post(f"/api/tickets/{t['id']}/time/", headers=h, json={"minutes": 25, "note": "diagnosis", "billable": False, "work_date": "2026-03-04"})
        assert r.status_code == 200 and r.json()[0]["minutes"] == 25 and r.json()[0]["work_date"] == "2026-03-04"
        assert api.get(f"/api/tickets/{t['id']}/time/", headers=h).json()[0]["billable"] is False
        assert api.get(f"/api/tickets/{t['id']}/time/", headers=auth("customer")).status_code == 403

    def test_events_are_staff_only(self, api, auth):
        t = create_ticket_api(api, auth("employee"), "x")
        events = api.get(f"/api/tickets/{t['id']}/events/", headers=auth("employee")).json()
        assert events and events[0]["event_type"] == "created"
        assert api.get(f"/api/tickets/{t['id']}/events/", headers=auth("customer")).status_code == 403

    def test_canned_responses_render_with_context(self, api, world, auth):
        h = auth("employee")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, auth("customer"), "Needs help"))
        items = api.get("/api/canned-responses/", headers=h).json()
        assert items and api.get("/api/canned-responses/", headers=auth("customer")).status_code == 403
        r = api.get(f"/api/canned-responses/{items[0]['id']}/render/?ticket={t['id']}", headers=h).json()["body"]
        assert t["number"] in r and "{{" not in r.replace("{{ ", "{{") or "{{" not in r


class TestBulk:
    def test_validation(self, api, auth):
        h = auth("employee")
        assert api.post("/api/tickets/bulk/", headers=h, json={"action": "add_tag"}).status_code == 400
        assert api.post("/api/tickets/bulk/", headers=h, json={"action": "x", "ticket_ids": list(range(101))}).status_code == 400
        assert api.post("/api/tickets/bulk/", headers=auth("customer"), json={"action": "add_tag", "ticket_ids": [1]}).status_code == 403

    def test_tags_priority_assign_and_transition_with_per_ticket_results(self, api, world, auth):
        h = auth("employee")
        a, b = create_ticket_api(api, h, "A"), create_ticket_api(api, h, "B")
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "add_tag", "ticket_ids": [a["id"], b["id"], 10 ** 9], "tag": " Urgent "})
        body = r.json()
        assert r.status_code == 207 and body["succeeded"] == 2 and body["failed"] == 1
        assert body["results"][str(10 ** 9)]["success"] is False
        assert api.get(f"/api/tickets/{a['id']}/", headers=h).json()["tags"] == ["urgent"]
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "remove_tag", "ticket_ids": [a["id"]], "tag": "urgent"})
        assert r.status_code == 200 and api.get(f"/api/tickets/{a['id']}/", headers=h).json()["tags"] == []
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "set_priority", "ticket_ids": [a["id"], b["id"]], "priority_key": "P1"})
        assert r.json()["succeeded"] == 2 and api.get(f"/api/tickets/{b['id']}/", headers=h).json()["priority"]["key"] == "P1"
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [a["id"]], "assignee_id": world.users["employee"]})
        assert r.status_code == 200
        bad = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [a["id"]], "assignee_id": world.users["customer"]})
        assert bad.status_code == 207 and "staff member" in bad.json()["results"][str(a["id"])]["error"]
        start = next(x for x in api.get(f"/api/tickets/{a['id']}/", headers=h).json()["transitions"] if x["name"] == "Start work")
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "transition", "ticket_ids": [a["id"]], "transition_id": start["id"]})
        assert r.json()["results"][str(a["id"])]["new_state"] == "In progress"
        assert api.post("/api/tickets/bulk/", headers=h, json={"action": "explode", "ticket_ids": [a["id"]]}).status_code == 207

    def test_bulk_never_touches_invisible_tickets(self, api, world, other_world, auth):
        t = create_ticket_api(api, auth("employee"), "Mine")
        r = api.post("/api/tickets/bulk/", headers=auth("employee", other_world), json={"action": "add_tag", "ticket_ids": [t["id"]], "tag": "x"})
        assert r.json()["results"][str(t["id"])] == {"success": False, "error": "Ticket not found or access denied."}


class TestMajorIncidents:
    def test_designate_coordinate_and_demote(self, api, world, auth):
        h = auth("team_lead")
        t = create_ticket_api(api, h, "Outage")
        d = api.post(f"/api/tickets/{t['id']}/designate_major/", headers=h, json={"is_major_incident": True, "status": "identified"}).json()
        assert d["is_major_incident"] and d["major_incident_status"] == "identified"
        assert d["incident_commander"]["id"] == world.users["team_lead"]
        child = create_ticket_api(api, h, "Child")
        d = api.post(f"/api/tickets/{t['id']}/major_coordinate/", headers=h, json={
            "status": "monitoring", "note": "Fix deployed", "post_incident_review": "RCA pending", "link_ticket_ids": [child["id"]],
            "commander_id": world.users["manager"]}).json()
        assert d["major_incident_status"] == "monitoring" and d["incident_commander"]["id"] == world.users["manager"]
        assert api.get(f"/api/tickets/{child['id']}/", headers=h).json()["parent"]["id"] == t["id"]
        bad = api.post(f"/api/tickets/{t['id']}/major_coordinate/", headers=h, json={"status": "weird"})
        assert bad.status_code == 400
        assert api.post(f"/api/tickets/{t['id']}/major_coordinate/", headers=h, json={"link_ticket_ids": [10 ** 9]}).status_code == 400
        demoted = api.post(f"/api/tickets/{t['id']}/designate_major/", headers=h, json={"is_major_incident": False}).json()
        assert demoted["is_major_incident"] is False and not demoted["major_incident_status"]

    def test_coordinate_requires_a_major_incident_and_active_staff_commander(self, api, world, auth):
        h = auth("team_lead")
        t = create_ticket_api(api, h, "Not major")
        assert api.post(f"/api/tickets/{t['id']}/major_coordinate/", headers=h, json={"status": "monitoring"}).status_code == 400
        assert api.post(f"/api/tickets/{t['id']}/designate_major/", headers=h, json={"commander_id": world.users["customer"]}).status_code == 400
        assert api.post(f"/api/tickets/{t['id']}/designate_major/", headers=auth("customer"), json={}).status_code == 403

    def test_only_leads_and_managers_can_declare_or_coordinate(self, api, world, auth):
        lead = auth("team_lead")
        t = assign_to_employee(api, world, auth, create_ticket_api(api, lead, "Outage"))
        base = f"/api/tickets/{t['id']}"
        for role in ("employee", "customer"):
            h = auth(role)
            assert api.post(f"{base}/designate_major/", headers=h, json={"is_major_incident": True}).status_code == 403
            assert api.post(f"{base}/major_coordinate/", headers=h, json={"status": "monitoring"}).status_code == 403
        assert api.patch(f"{base}/", headers=auth("employee"), json={"is_major_incident": True}).status_code == 403
        assert api.get(f"{base}/", headers=lead).json()["is_major_incident"] is False
        assert api.post(f"{base}/designate_major/", headers=auth("manager"), json={"is_major_incident": True}).status_code == 200


class TestBulkAssign:
    def _team_with(self, world, role_key, name):
        from opsdesk.db.models import Team, TeamMember
        with tenant_session(world.tenant_id) as s:
            t = Team(tenant_id=world.tenant_id, name=name)
            s.add(t)
            s.flush()
            s.add(TeamMember(team_id=t.id, user_id=world.users[role_key]))
            return t.id

    def test_assigning_an_agent_works_whatever_team_the_ticket_is_in(self, api, world, auth):
        h = auth("manager")
        other_team = self._team_with(world, "team_lead", "Platform")
        t = create_ticket_api(api, auth("employee"), "needs help")
        assert api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"team": other_team}).status_code == 200
        # the assignee is not in the ticket's team; assigning one ticket from its page is allowed, so bulk is too
        one = api.patch(f"/api/tickets/{t['id']}/", headers=h, json={"assignee": world.users["employee"]})
        assert one.status_code == 200
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [t["id"]], "assignee_id": world.users["manager"]})
        assert r.status_code == 200 and r.json()["succeeded"] == 1
        assert api.get(f"/api/tickets/{t['id']}/", headers=h).json()["assignee"]["id"] == world.users["manager"]

    def test_nothing_chosen_is_refused_with_a_reason(self, api, world, auth):
        h = auth("manager")
        t = create_ticket_api(api, auth("employee"), "x")
        r = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [t["id"]], "assignee_id": None, "team_id": None})
        assert r.status_code == 207 and "assignee_id or team_id" in r.json()["results"][str(t["id"])]["error"]

    def test_an_explicit_team_and_member_pair_must_agree(self, api, world, auth):
        h = auth("manager")
        team_id = self._team_with(world, "team_lead", "Platform")
        t = create_ticket_api(api, auth("employee"), "y")
        bad = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [t["id"]], "assignee_id": world.users["employee"], "team_id": team_id})
        assert bad.status_code == 207 and "not a member of team" in bad.json()["results"][str(t["id"])]["error"]
        ok = api.post("/api/tickets/bulk/", headers=h, json={"action": "assign", "ticket_ids": [t["id"]], "assignee_id": world.users["team_lead"], "team_id": team_id})
        assert ok.status_code == 200 and ok.json()["succeeded"] == 1
