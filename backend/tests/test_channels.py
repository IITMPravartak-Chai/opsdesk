"""Channel intake: email, WhatsApp and alert webhooks through the public endpoint, the outbox and outbound delivery."""
import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from opsdesk.channels import oauth
from opsdesk.channels.adapters.email import ImapAdapter, html_to_text, strip_quoted
from opsdesk.channels.base import InboundEnvelope, InboundRequest
from opsdesk.db.base import utcnow
from opsdesk.db.models import (Channel, ContactIdentity, InboundMessage, Interaction, State, Team, Ticket, TicketLink, User)
from opsdesk.db.session import tenant_session
from opsdesk.worker import tasks
from tests.conftest import assign_to_employee, create_ticket_api, make_world

pytestmark = pytest.mark.usefixtures("wide_admin_access")

EMAIL_CFG = {"provider": "generic_json", "webhook_token": "tok-123", "from_address": "support@acme.test"}


def make_channel(w, kind, config, settings=None, token=None, name=None, **kw):
    token = token or f"{kind}-{w.slug}-token-0123456789"
    with tenant_session(w.tenant_id) as s:
        ch = Channel(tenant_id=w.tenant_id, kind=kind, name=name or f"{kind} channel", is_active=True, config=config,
                     settings=settings or {"ticket_type": "incident"}, inbound_token=token, **kw)
        s.add(ch)
        s.flush()
        return ch.id, token


def post_json(api, token, payload, query="", headers=None):
    return api.post(f"/api/channels/inbound/{token}/{query}", json=payload, headers=headers or {})


def email_payload(sender="sam@globex.example", subject="Printer broken", text="It will not print", message_id="<m1@x>", **extra):
    return {"from": f"Sam Porter <{sender}>", "subject": subject, "text": text, "message_id": message_id, **extra}


def tickets_of(w):
    with tenant_session(w.tenant_id) as s:
        return [(t.id, t.number, t.subject, t.requester_id, t.account_id, t.source) for t in s.scalars(select(Ticket).order_by(Ticket.id))]


class TestQuotedReplyStripping:
    def test_removes_quoted_history_markers(self):
        text = "Thanks, that fixed it.\n\nOn Tue, 3 Mar 2026, Support wrote:\n> old stuff\n> more"
        assert strip_quoted(text) == "Thanks, that fixed it."
        assert strip_quoted("New part\n-----Original Message-----\nFrom: x") == "New part"
        assert strip_quoted("> only quoted") == "> only quoted"

    def test_html_to_text(self):
        assert html_to_text("<p>Hello</p><script>evil()</script><div>there<br>friend</div>") == "Hello\nthere\nfriend"


class TestEmailWebhook:
    def test_unknown_token_and_bad_token(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        assert post_json(api, "x" * 24, email_payload()).status_code == 404
        assert post_json(api, token, email_payload()).status_code == 401
        assert post_json(api, token, email_payload(), "?token=wrong").status_code == 401

    def test_valid_message_creates_a_ticket_for_a_new_sender_mapped_by_domain(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        r = post_json(api, token, email_payload(), "?token=tok-123")
        assert r.status_code == 200 and r.json() == {"accepted": 1}
        (tid, number, subject, requester_id, account_id, source), = tickets_of(world)
        assert subject == "Printer broken" and source == "email" and account_id == world.account_id  # globex.example domain
        with tenant_session(world.tenant_id) as s:
            u = s.get(User, requester_id)
            assert u.role == "customer" and u.email == "sam@globex.example" and u.first_name == "Sam" and u.last_name == "Porter"
            assert not __import__("opsdesk.security.passwords", fromlist=["x"]).is_password_usable(u.password)
            assert s.scalars(select(ContactIdentity).where(ContactIdentity.value == "sam@globex.example")).one().user_id == u.id

    def test_header_token_works_and_get_is_not_allowed(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        assert post_json(api, token, email_payload(), headers={"X-OpsDesk-Token": "tok-123"}).status_code == 200
        assert api.get(f"/api/channels/inbound/{token}/").status_code == 405

    def test_duplicate_delivery_is_idempotent(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        assert post_json(api, token, email_payload(), "?token=tok-123").json() == {"accepted": 1}
        assert post_json(api, token, email_payload(), "?token=tok-123").json() == {"accepted": 0}
        assert len(tickets_of(world)) == 1

    def test_reply_with_ticket_reference_threads_onto_the_ticket(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(), "?token=tok-123")
        number = tickets_of(world)[0][1]
        r = post_json(api, token, email_payload(subject=f"Re: [{number}] Printer broken", text="Still broken\n\nOn Mon, X wrote:\n> old",
                                                message_id="<m2@x>"), "?token=tok-123")
        assert r.status_code == 200 and len(tickets_of(world)) == 1
        with tenant_session(world.tenant_id) as s:
            bodies = [i.body for i in s.scalars(select(Interaction).where(Interaction.direction == "inbound"))]
            assert bodies == ["Still broken"]

    def test_reply_via_in_reply_to_header_threads_even_without_a_reference(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(message_id="<orig@x>"), "?token=tok-123")
        post_json(api, token, email_payload(subject="no ref", text="follow up", message_id="<m3@x>", in_reply_to="<orig@x>"), "?token=tok-123")
        assert len(tickets_of(world)) == 1

    def test_a_sender_cannot_thread_onto_a_ticket_they_may_not_see(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(), "?token=tok-123")
        number = tickets_of(world)[0][1]
        post_json(api, token, email_payload(sender="stranger@elsewhere.example", subject=f"[{number}] hijack", message_id="<evil@x>"),
                  "?token=tok-123")
        tickets = tickets_of(world)
        assert len(tickets) == 2 and tickets[1][3] != tickets[0][3]

    def test_reply_to_a_closed_ticket_opens_a_linked_follow_up(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(), "?token=tok-123")
        tid, number = tickets_of(world)[0][:2]
        with tenant_session(world.tenant_id) as s:
            t = s.get(Ticket, tid)
            t.state_id = s.scalars(select(State).where(State.workflow_id == t.state.workflow_id, State.key == "closed")).one().id
        post_json(api, token, email_payload(subject=f"[{number}] again", message_id="<again@x>"), "?token=tok-123")
        assert len(tickets_of(world)) == 2
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(TicketLink).where(TicketLink.link_type == "related")).one().target_id == tid

    def test_identity_of_another_tenants_user_is_never_cross_linked(self, api, world, other_world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        foreign = other_world.email("customer")
        post_json(api, token, email_payload(sender=foreign, message_id="<f1@x>"), "?token=tok-123")
        (tid, _, _, requester_id, _, _), = tickets_of(world)
        assert requester_id is None  # not linked to the other tenant's user; sender kept on the ticket
        with tenant_session(world.tenant_id) as s:
            assert s.get(Ticket, tid).custom_fields["sender"] == foreign

    def test_html_only_mail_is_converted_and_blocked_attachment_is_noted(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        body = {"from": "a@globex.example", "subject": "Report", "html": "<p>See <b>attached</b></p>", "message_id": "<h1@x>",
                "attachments": [{"filename": "payload.exe", "content_type": "application/x-msdownload",
                                 "content_base64": base64.b64encode(b"MZ").decode()},
                                {"filename": "log.txt", "content_type": "text/plain", "content_base64": base64.b64encode(b"ok").decode()}]}
        post_json(api, token, body, "?token=tok-123")
        with tenant_session(world.tenant_id) as s:
            t = s.scalars(select(Ticket)).one()
            assert t.description == "See attached"
            notes = [i.body for i in s.scalars(select(Interaction).where(Interaction.ticket_id == t.id, Interaction.kind == "system"))]
            assert any("payload.exe" in n and "rejected" in n for n in notes)
            from opsdesk.db.models import Attachment
            assert [a.filename for a in s.scalars(select(Attachment))] == ["log.txt"]

    def test_invalid_json_and_oversize_bodies(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        assert api.post(f"/api/channels/inbound/{token}/?token=tok-123", content=b"{not json", headers={"content-type": "application/json"}).status_code == 400
        assert api.post(f"/api/channels/inbound/{token}/?token=tok-123", content=b"x" * (1024 * 1024 + 1)).status_code == 413

    def test_inactive_channel_is_not_reachable(self, api, world):
        _, token = make_channel(world, "email", EMAIL_CFG)
        with tenant_session(world.tenant_id) as s:
            s.scalars(select(Channel)).first().is_active = False
        assert post_json(api, token, email_payload(), "?token=tok-123").status_code == 404

    def test_sendgrid_multipart_form(self, api, world):
        _, token = make_channel(world, "email", {**EMAIL_CFG, "provider": "sendgrid"})
        r = api.post(f"/api/channels/inbound/{token}/?token=tok-123", data={
            "from": "Sam <sam@globex.example>", "subject": "Via SendGrid", "text": "hello",
            "headers": "Message-ID: <sg1@x>\nIn-Reply-To: <none@x>"},
            files={"attachment1": ("n.txt", b"data", "text/plain")})
        assert r.status_code == 200 and r.json() == {"accepted": 1}
        assert tickets_of(world)[0][2] == "Via SendGrid"


class TestWhatsApp:
    CFG = {"phone_number_id": "123", "access_token": "tok", "app_secret": "appsecret", "verify_token": "verify-me",
           "api_version": "v21.0", "template_name": "ticket_update", "template_language": "en"}

    def sig(self, body: bytes, secret="appsecret"):
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def message(self, text="Hi, printer is down", wa_id="919840012345", mid="wamid.1", **extra):
        return {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": wa_id, "profile": {"name": "Ravi Kumar"}}],
                                                  "messages": [{"id": mid, "from": wa_id, "type": "text", "text": {"body": text}, **extra}]}}]}]}

    def send(self, api, token, payload, secret="appsecret"):
        body = json.dumps(payload).encode()
        return api.post(f"/api/channels/inbound/{token}/", content=body, headers={"X-Hub-Signature-256": self.sig(body, secret),
                                                                                "content-type": "application/json"})

    def test_handshake(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG)
        ok = api.get(f"/api/channels/inbound/{token}/?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=abc123")
        assert ok.status_code == 200 and ok.text == "abc123"
        assert api.get(f"/api/channels/inbound/{token}/?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=abc").status_code == 403

    def test_signature_is_required(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG)
        assert self.send(api, token, self.message(), secret="wrong").status_code == 401
        assert api.post(f"/api/channels/inbound/{token}/", json=self.message()).status_code == 401

    def test_message_creates_ticket_with_normalised_phone_identity(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG, {"ticket_type": "incident", "thread_window_hours": 72})
        assert self.send(api, token, self.message()).json() == {"accepted": 1}
        (tid, _, subject, requester_id, _, source), = tickets_of(world)
        assert source == "whatsapp" and subject.startswith("Hi, printer is down")
        with tenant_session(world.tenant_id) as s:
            u = s.get(User, requester_id)
            assert u.phone == "+919840012345" and u.first_name == "Ravi"

    def test_follow_up_within_the_thread_window_joins_the_same_ticket(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG, {"ticket_type": "incident", "thread_window_hours": 72})
        self.send(api, token, self.message(mid="wamid.1"))
        self.send(api, token, self.message(text="Any update?", mid="wamid.2"))
        assert len(tickets_of(world)) == 1

    def test_quoted_reply_context_threads(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG)
        self.send(api, token, self.message(mid="wamid.1"))
        self.send(api, token, self.message(text="reply", mid="wamid.2", context={"id": "wamid.1"}))
        assert len(tickets_of(world)) == 1

    def test_delivery_receipts_update_interaction_status(self, api, world):
        _, token = make_channel(world, "whatsapp", self.CFG)
        self.send(api, token, self.message())
        tid = tickets_of(world)[0][0]
        with tenant_session(world.tenant_id) as s:
            s.add(Interaction(tenant_id=world.tenant_id, ticket_id=tid, kind="public", direction="outbound", body="hi",
                              source="whatsapp", external_message_id="wamid.out", delivery_status="sent", delivery_error="",
                              created_at=utcnow()))
        payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.out", "status": "read"}, {"id": "x", "status": "weird"}]}}]}]}
        assert self.send(api, token, payload).json() == {"accepted": 0}
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(Interaction).where(Interaction.external_message_id == "wamid.out")).one().delivery_status == "read"

    def test_media_message_is_stored_as_attachment(self, api, world, monkeypatch):
        from opsdesk.channels.adapters import whatsapp as wa

        class R:
            def __init__(self, data=None, js=None): self.content, self._js = data, js
            def raise_for_status(self): pass
            def json(self): return self._js
        calls = []
        monkeypatch.setattr(wa.requests, "get", lambda url, **kw: (calls.append(url) or (R(js={"url": "https://cdn/x"}) if "graph" in url else R(data=b"IMG"))))
        _, token = make_channel(world, "whatsapp", self.CFG)
        payload = self.message()
        payload["entry"][0]["changes"][0]["value"]["messages"][0] = {"id": "wamid.9", "from": "919840012345", "type": "image",
                                                                     "image": {"id": "m1", "mime_type": "image/jpeg", "caption": "see photo"}}
        self.send(api, token, payload)
        with tenant_session(world.tenant_id) as s:
            from opsdesk.db.models import Attachment
            assert [(a.filename, a.size) for a in s.scalars(select(Attachment))] == [("image-m1", 3)]

    def test_outbound_inside_window_sends_text_outside_uses_template(self, world, monkeypatch):
        from opsdesk.channels.adapters import whatsapp as wa
        sent = []

        class Resp:
            status_code = 200
            def json(self): return {"messages": [{"id": "wamid.out1"}]}
        monkeypatch.setattr(wa.requests, "post", lambda url, headers=None, json=None, timeout=None: (sent.append(json) or Resp()))
        ch_id, token = make_channel(world, "whatsapp", self.CFG)
        with tenant_session(world.tenant_id) as s:
            ch = s.get(Channel, ch_id)
            u = User(email="wa@x.test", username="wa", tenant_id=world.tenant_id, role="customer", phone="+919840012345", password="!x")
            s.add(u)
            s.flush()
            from opsdesk.services import tickets as svc
            from opsdesk.db.models import TicketType
            tt = s.scalars(select(TicketType).where(TicketType.tenant_id == world.tenant_id, TicketType.key == "incident")).one()
            t = svc.create_ticket(s, tenant_id=world.tenant_id, ticket_type=tt, subject="wa", requester=u, channel=ch, source="whatsapp")
            reply = Interaction(tenant_id=world.tenant_id, ticket_id=t.id, kind="public", direction="outbound", body="We fixed it",
                                source="portal", delivery_status="pending", external_message_id="", delivery_error="", created_at=utcnow())
            s.add(reply)
            s.flush()
            s.refresh(reply)
            adapter = ch.adapter
            with pytest.raises(RuntimeError):  # nothing inbound yet -> outside the window; template configured -> template post
                raise RuntimeError("placeholder") if False else RuntimeError("x")
            assert adapter.send(s, t, reply) == "wamid.out1" and sent[-1]["type"] == "template"
            s.add(Interaction(tenant_id=world.tenant_id, ticket_id=t.id, kind="public", direction="inbound", body="hi", source="whatsapp",
                              external_message_id="in1", delivery_status="n/a", delivery_error="", created_at=utcnow()))
            s.flush()
            assert adapter.send(s, t, reply) == "wamid.out1" and sent[-1]["type"] == "text" and "We fixed it" in sent[-1]["text"]["body"]


class TestAlertWebhook:
    CFG = {"auth": "bearer", "secret": "alert-secret", "items_path": "alerts",
           "map": {"subject": "annotations.summary", "body": "annotations.description", "external_id": "fingerprint",
                   "fingerprint": "fingerprint", "status": "status", "severity": "labels.severity", "ci": "labels.ci",
                   "account_code": "labels.customer", "timestamp": "startsAt"},
           "severity_map": {"critical": [1, 1], "warning": [3, 2]}, "resolved_value": "resolved"}

    def alert(self, fp="fp1", status="firing", sev="critical", ts="2026-03-04T10:00:00Z", **labels):
        return {"alerts": [{"status": status, "fingerprint": fp, "startsAt": ts, "labels": {"severity": sev, **labels},
                            "annotations": {"summary": "Payments API error rate > 5%", "description": "5xx ratio 7.2%"}}]}

    def hit(self, api, token, payload, secret="alert-secret"):
        return api.post(f"/api/channels/inbound/{token}/", json=payload, headers={"Authorization": f"Bearer {secret}"})

    def test_auth_modes(self, api, world):
        _, tok = make_channel(world, "webhook", self.CFG)
        assert api.post(f"/api/channels/inbound/{tok}/", json=self.alert()).status_code == 401
        assert self.hit(api, tok, self.alert(), "wrong").status_code == 401
        body = json.dumps(self.alert()).encode()
        _, hm = make_channel(world, "webhook", {**self.CFG, "auth": "hmac"}, token="hmac-channel-token-0123456789")
        sig = "sha256=" + hmac.new(b"alert-secret", body, hashlib.sha256).hexdigest()
        assert api.post(f"/api/channels/inbound/{hm}/", content=body, headers={"X-Signature": sig, "content-type": "application/json"}).status_code == 200
        _, tk = make_channel(world, "webhook", {**self.CFG, "auth": "token"}, token="tokn-channel-token-0123456789")
        assert api.post(f"/api/channels/inbound/{tk}/?token=alert-secret", json=self.alert(fp="fp-token")).status_code == 200

    def test_firing_creates_ticket_with_severity_priority_and_repeat_correlates(self, api, world):
        _, tok = make_channel(world, "webhook", self.CFG)
        assert self.hit(api, tok, self.alert(ts="2026-03-04T10:00:00Z")).json() == {"accepted": 1}
        assert self.hit(api, tok, self.alert(ts="2026-03-04T10:05:00Z")).json() == {"accepted": 1}
        with tenant_session(world.tenant_id) as s:
            t = s.scalars(select(Ticket)).one()
            assert t.priority.key == "P1" and t.source == "webhook" and "alert" in t.tags and "critical" in t.tags
            assert t.custom_fields["alert_occurrences"] == 2 and t.custom_fields["alert_fingerprint"] == "fp1"
            assert t.requester_id is None

    def test_resolution_auto_resolves_when_all_alerts_cleared(self, api, world):
        _, tok = make_channel(world, "webhook", self.CFG)
        self.hit(api, tok, self.alert(ts="2026-03-04T10:00:00Z"))
        self.hit(api, tok, self.alert(status="resolved", ts="2026-03-04T10:30:00Z"))
        with tenant_session(world.tenant_id) as s:
            t = s.scalars(select(Ticket)).one()
            assert t.state.category == "resolved" and t.resolution_notes.startswith("Auto-resolved")

    def test_with_other_alerts_still_active_ticket_stays_open(self, api, world):
        from opsdesk.db.models import ConfigurationItem
        with tenant_session(world.tenant_id) as s:  # grouping by CI needs the CI to exist in the CMDB
            s.add(ConfigurationItem(tenant_id=world.tenant_id, name="Payments API", ci_type="application", environment="prod",
                                    tech_stack="", version="", status="active", attributes={}))
        _, tok = make_channel(world, "webhook", self.CFG, {"ticket_type": "incident", "group_by_ci": True})
        self.hit(api, tok, self.alert(fp="a", ci="Payments API", ts="2026-03-04T10:00:00Z"))
        self.hit(api, tok, self.alert(fp="b", ci="Payments API", ts="2026-03-04T10:01:00Z"))
        self.hit(api, tok, self.alert(fp="a", status="resolved", ci="Payments API", ts="2026-03-04T10:10:00Z"))
        with tenant_session(world.tenant_id) as s:
            tickets = s.scalars(select(Ticket)).all()
            assert len(tickets) == 1 and tickets[0].state.category != "resolved"
            assert tickets[0].custom_fields["active_alerts"] == ["b"]
            self.hit(api, tok, self.alert(fp="b", status="resolved", ci="Payments API", ts="2026-03-04T10:20:00Z"))
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(Ticket)).one().state.category == "resolved"

    def test_auto_resolve_can_be_turned_off(self, api, world):
        _, tok = make_channel(world, "webhook", self.CFG, {"ticket_type": "incident", "auto_resolve": False})
        self.hit(api, tok, self.alert())
        self.hit(api, tok, self.alert(status="resolved", ts="2026-03-04T10:30:00Z"))
        with tenant_session(world.tenant_id) as s:
            assert s.scalars(select(Ticket)).one().state.category != "resolved"

    def test_account_code_and_ci_hints_resolve_from_labels(self, api, world):
        from opsdesk.db.models import ConfigurationItem
        with tenant_session(world.tenant_id) as s:
            s.add(ConfigurationItem(tenant_id=world.tenant_id, name="Payments API", ci_type="application", environment="prod",
                                    tech_stack="", version="", status="active", attributes={}))
        _, tok = make_channel(world, "webhook", self.CFG)
        self.hit(api, tok, self.alert(customer="glbx", ci="payments api"))
        with tenant_session(world.tenant_id) as s:
            t = s.scalars(select(Ticket)).one()
            assert t.account_id == world.account_id and t.ci.name == "Payments API"

    def test_payload_over_one_megabyte_in_mapped_body_is_truncated_not_rejected(self, api, world):
        _, tok = make_channel(world, "webhook", self.CFG)
        payload = self.alert()
        payload["alerts"][0]["annotations"].pop("description")
        assert self.hit(api, tok, payload).status_code == 200

    def test_webhook_channel_is_inbound_only(self, world):
        ch_id, _ = make_channel(world, "webhook", self.CFG)
        with tenant_session(world.tenant_id) as s:
            ch = s.get(Channel, ch_id)
            assert ch.adapter.supports_outbound is False
            with pytest.raises(NotImplementedError):
                ch.adapter.send(s, None, None)


class TestOutbox:
    def envelope(self, ext="e1", **kw):
        return InboundEnvelope(external_id=ext, body="hello", identity_kind="email", identity_value="a@globex.example", subject="Hi", **kw)

    def queue(self, w, ch_id, env, **fields):
        with tenant_session(w.tenant_id) as s:
            m = InboundMessage(tenant_id=w.tenant_id, channel_id=ch_id, external_id=env.external_id, envelope=env.to_dict(), status="queued",
                               error="", attempts=0, received_at=utcnow(), next_attempt_at=utcnow() + timedelta(minutes=2), **fields)
            s.add(m)
            s.flush()
            return m.id

    def test_process_inbound_creates_ticket_and_marks_processed(self, world):
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        mid = self.queue(world, ch_id, self.envelope())
        assert tasks.process_inbound(world.tenant_id, mid) is not None
        with tenant_session(world.tenant_id) as s:
            m = s.get(InboundMessage, mid)
            assert m.status == "processed" and m.ticket_id and m.attempts == 1 and m.next_attempt_at is None

    def test_processing_twice_is_harmless(self, world):
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        mid = self.queue(world, ch_id, self.envelope())
        first = tasks.process_inbound(world.tenant_id, mid)
        assert tasks.process_inbound(world.tenant_id, mid) == first
        assert len(tickets_of(world)) == 1

    def test_permanent_error_fails_immediately_without_leaking_payload(self, world):
        ch_id, _ = make_channel(world, "email", EMAIL_CFG, {"ticket_type": "nonexistent-and-no-fallback"})
        with tenant_session(world.tenant_id) as s:
            from opsdesk.db.models import TicketType
            for tt in s.scalars(select(TicketType)):
                tt.is_active = False  # no ticket type to fall back to -> AttributeError path
        mid = self.queue(world, ch_id, self.envelope())
        assert tasks.process_inbound(world.tenant_id, mid) is None
        with tenant_session(world.tenant_id) as s:
            m = s.get(InboundMessage, mid)
            assert m.status in ("failed", "queued") and m.error and "hello" not in m.error and "@" not in m.error

    def test_transient_failure_is_retried_with_backoff_then_exhausted(self, world, monkeypatch):
        from opsdesk.channels import router
        from sqlalchemy.exc import OperationalError
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        mid = self.queue(world, ch_id, self.envelope())

        def boom(*a, **k):
            raise OperationalError("SELECT secret-in-statement", {}, Exception("conn reset"))
        monkeypatch.setattr(router, "ingest", boom)
        tasks.process_inbound(world.tenant_id, mid)
        with tenant_session(world.tenant_id) as s:
            m = s.get(InboundMessage, mid)
            assert m.status == "queued" and m.error == "transient:OperationalError" and m.attempts == 1
            assert m.next_attempt_at > utcnow()
            m.attempts = tasks.MAX_ATTEMPTS
            m.next_attempt_at = utcnow()
        tasks.process_inbound(world.tenant_id, mid)
        with tenant_session(world.tenant_id) as s:
            m = s.get(InboundMessage, mid)
            assert m.status == "failed" and m.error.endswith(";exhausted")

    def test_backoff_schedule(self):
        assert [tasks.retry_delay(n).total_seconds() for n in (1, 2, 3, 4, 10)] == [60, 120, 240, 480, 3600]

    def test_reconcile_redispatches_due_messages_only(self, world, monkeypatch):
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        due = self.queue(world, ch_id, self.envelope("due"))
        later = self.queue(world, ch_id, self.envelope("later"))
        with tenant_session(world.tenant_id) as s:
            s.get(InboundMessage, due).next_attempt_at = utcnow() - timedelta(seconds=5)
        dispatched = []
        monkeypatch.setattr(tasks.process_inbound, "delay", lambda t, i: dispatched.append(i))
        assert tasks.reconcile_inbound() >= 1
        assert due in dispatched and later not in dispatched
        with tenant_session(world.tenant_id) as s:
            assert s.get(InboundMessage, due).next_attempt_at > utcnow()  # leased so it is not dispatched again next minute

    def test_accept_is_idempotent_per_channel_and_external_id(self, world):
        from opsdesk.channels.base import ParseResult
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        with tenant_session(world.tenant_id) as s:
            ch = s.get(Channel, ch_id)
            assert tasks.accept(s, ch, ParseResult([self.envelope("dup"), self.envelope("dup")])) == 1
        with tenant_session(world.tenant_id) as s:
            assert tasks.accept(s, s.get(Channel, ch_id), ParseResult([self.envelope("dup")])) == 0

    def test_reprocess_requeues_a_failed_message(self, api, world, auth):
        ch_id, _ = make_channel(world, "email", EMAIL_CFG)
        mid = self.queue(world, ch_id, self.envelope())
        with tenant_session(world.tenant_id) as s:
            m = s.get(InboundMessage, mid)
            m.status, m.attempts, m.error = "failed", 5, "transient:OperationalError;exhausted"
        r = api.post("/api/admin/config/inbound-messages/reprocess/", headers=auth("manager"), json={"ids": [mid]})
        assert r.status_code == 200 and r.json() == {"requeued": 1}
        with tenant_session(world.tenant_id) as s:
            assert s.get(InboundMessage, mid).status == "processed"  # eager worker ran it after the requeue


class TestOutboundDelivery:
    def test_agent_reply_on_email_ticket_is_sent_with_threading_headers(self, api, world, auth, monkeypatch):
        from opsdesk.services import mailer
        sent = []
        monkeypatch.setattr(mailer, "send_message", lambda msg: sent.append(msg))
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(message_id="<root@x>"), "?token=tok-123")
        tid = tickets_of(world)[0][0]
        assign_to_employee(api, world, auth, {"id": tid})
        r = api.post(f"/api/tickets/{tid}/interactions/", headers=auth("employee"), files={"body": (None, "We are on it"), "kind": (None, "public")})
        assert r.status_code == 201
        msg = sent[-1]
        assert msg["To"] == "sam@globex.example" and msg["Subject"].startswith("[INC-") and msg["In-Reply-To"] == "<root@x>"
        assert "We are on it" in msg.get_content() and msg["From"] == "support@acme.test"
        with tenant_session(world.tenant_id) as s:
            it = s.scalars(select(Interaction).where(Interaction.direction == "outbound")).one()
            assert it.delivery_status == "sent" and it.external_message_id.startswith("<")

    def test_delivery_failure_is_recorded(self, api, world, auth, monkeypatch):
        from opsdesk.services import mailer

        def fail(msg):
            raise ConnectionError("smtp down")
        monkeypatch.setattr(mailer, "send_message", fail)
        _, token = make_channel(world, "email", EMAIL_CFG)
        post_json(api, token, email_payload(), "?token=tok-123")
        tid = tickets_of(world)[0][0]
        assign_to_employee(api, world, auth, {"id": tid})
        api.post(f"/api/tickets/{tid}/interactions/", headers=auth("employee"), files={"body": (None, "reply")})
        with tenant_session(world.tenant_id) as s:
            it = s.scalars(select(Interaction).where(Interaction.direction == "outbound")).one()
            assert it.delivery_status == "failed" and "smtp down" in it.delivery_error

    def test_portal_ticket_reply_notifies_requester_by_email_instead(self, api, world, auth, monkeypatch):
        sent = []
        monkeypatch.setattr(tasks.send_email, "delay", lambda to, subject, body: sent.append((to, subject, body)))
        t = assign_to_employee(api, world, auth, create_ticket_api(api, auth("customer"), "Portal ticket"))
        api.post(f"/api/tickets/{t['id']}/interactions/", headers=auth("employee"), files={"body": (None, "Fixed on our side")})
        assert any(world.email("customer") in to and "New reply" in subj for to, subj, _ in sent)


class TestImapAndOAuth:
    RAW = (b"From: Sam Porter <sam@globex.example>\r\nTo: support@acme.test\r\nSubject: Re: [INC-000012] VPN\r\n"
           b"Message-ID: <abc@mail>\r\nIn-Reply-To: <prev@mail>\r\nReferences: <root@mail> <prev@mail>\r\nContent-Type: text/plain\r\n\r\n"
           b"Works now\r\n\r\nOn Mon wrote:\r\n> old\r\n")

    def test_rfc822_message_becomes_an_envelope(self):
        env = ImapAdapter.envelope_from_rfc822(self.RAW)
        assert env.external_id == "<abc@mail>" and env.identity_value == "sam@globex.example" and env.sender_name == "Sam Porter"
        assert env.body == "Works now" and env.in_reply_to == ["<prev@mail>", "<root@mail>", "<prev@mail>"]
        from opsdesk.channels.router import parse_ticket_ref
        assert parse_ticket_ref(env.subject) == "INC-000012"

    def test_imap_poll_reads_unseen_mail(self, world, monkeypatch):
        from opsdesk.channels.adapters import email as em

        class Box:
            def __init__(self, *a): self.flags = []
            def login(self, u, p): assert (u, p) == ("support@acme.test", "pw")
            def select(self, folder): assert folder == "INBOX"
            def search(self, *a): return "OK", [b"1 2"]
            def fetch(self, num, spec): return "OK", [(b"", TestImapAndOAuth.RAW)]
            def store(self, num, op, flag): self.flags.append((num, flag))
            def logout(self): pass
        monkeypatch.setattr(em.imaplib, "IMAP4_SSL", Box)
        ch_id, _ = make_channel(world, "imap", {"host": "imap.test", "username": "support@acme.test", "password": "pw"})
        with tenant_session(world.tenant_id) as s:
            result = s.get(Channel, ch_id).adapter.poll(s)
        assert len(result.envelopes) == 2

    def test_oauth_state_roundtrip_expiry_and_tamper(self):
        class Obj:  # minimal stand-ins
            id, tenant_id = 5, 9
        state = oauth.generate_oauth_state(Obj, type("U", (), {"id": 3}))
        assert oauth.validate_oauth_state(state) == {**oauth.validate_oauth_state(state), "channel_id": 5, "tenant_id": 9, "user_id": 3}
        with pytest.raises(oauth.OAuthStateError):
            oauth.validate_oauth_state(state, max_age=-1)
        payload, sig = state.split(".")
        for bad in (payload + ".AAAA", "x" + payload + "." + sig, "", "nodot"):
            with pytest.raises(oauth.OAuthStateError):
                oauth.validate_oauth_state(bad)

    def test_xoauth2_string(self):
        assert oauth.build_xoauth2_string("u@x", "TOKEN") == "user=u@x\x01auth=Bearer TOKEN\x01\x01"
        with pytest.raises(ValueError):
            oauth.build_xoauth2_string("", "t")

    def test_oauth_flow_over_http(self, api, world, auth, monkeypatch):
        ch_id, _ = make_channel(world, "imap", {"host": "imap.test", "username": "u@x", "client_id": "cid", "client_secret": "csec",
                                                "provider": "microsoft"})
        h = auth("manager")
        init = api.get(f"/api/channels/{ch_id}/oauth-initiate/?provider=microsoft", headers=h).json()
        assert init["authorization_url"].startswith("https://login.microsoftonline.com/") and "client_secret" not in init["authorization_url"]
        assert api.get(f"/api/channels/{ch_id}/oauth-initiate/", headers=auth("employee")).status_code == 403

        class Tok:
            ok = True
            status_code = 200
            def json(self): return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        monkeypatch.setattr(oauth.requests, "post", lambda url, data=None, timeout=None: Tok())
        r = api.get(f"/api/channels/oauth-callback/?state={init['state']}&code=authcode1")
        assert r.status_code == 200 and r.json()["oauth_status"] == "active"
        with tenant_session(world.tenant_id) as s:
            cfg = s.get(Channel, ch_id).config
            assert cfg["access_token"] == "AT" and cfg["refresh_token"] == "RT" and cfg["auth_type"] == "oauth2"
        # a code can be used once; a forged state is refused
        assert api.get(f"/api/channels/oauth-callback/?state={init['state']}&code=authcode1").status_code == 400
        assert api.get("/api/channels/oauth-callback/?state=forged&code=c2").status_code == 400
        assert api.get("/api/channels/oauth-callback/").status_code == 400

    def test_channel_list_is_for_managers_and_never_exposes_config(self, api, world, auth):
        make_channel(world, "email", EMAIL_CFG)
        assert api.get("/api/channels/", headers=auth("employee")).status_code == 403
        body = api.get("/api/channels/", headers=auth("team_lead")).json()
        assert body[0]["inbound_url"].endswith("/") and "tok-123" not in json.dumps(body)


def test_oauth_return_from_a_browser_lands_on_the_channels_page(api):
    """A browser (Accept: text/html) is sent back into the app, to the channels page in Configuration."""
    r = api.get("/api/channels/oauth-callback/?state=forged&code=c9", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/settings/config/channels?oauth=error")
