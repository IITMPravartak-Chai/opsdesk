"""WhatsApp Business via Meta's WhatsApp Cloud API.

Inbound: Meta calls the webhook (GET verification handshake, then signed POSTs).
Outbound: free-form text inside the 24-hour customer-service window; outside it, a pre-approved message
template is used (Meta policy).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import requests
from sqlalchemy import select

from ...db.base import utcnow
from ...db.models import Interaction
from ..base import ChannelAdapter, HandshakeResponse, InboundEnvelope, InboundRequest, ParseResult

GRAPH = "https://graph.facebook.com"
STATUS_MAP = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed"}


class WhatsAppCloudAdapter(ChannelAdapter):
    kind = "whatsapp"
    label = "WhatsApp Business (Cloud API)"
    source = "whatsapp"
    config_help = {
        "phone_number_id": "From Meta App > WhatsApp > API setup",
        "access_token": "Permanent System User token with whatsapp_business_messaging",
        "app_secret": "Meta App secret (verifies X-Hub-Signature-256)",
        "verify_token": "Any random string; enter the same in Meta's webhook config",
        "api_version": "e.g. v21.0",
        "template_name": "Approved template for replies outside the 24h window, with 2 body params: ticket number, message",
        "template_language": "e.g. en",
    }

    @property
    def _base(self):
        return f"{GRAPH}/{self.config.get('api_version', 'v21.0')}"

    def handshake(self, request: InboundRequest):
        if request.method != "GET":
            return None
        if (request.query.get("hub.mode") == "subscribe"
                and hmac.compare_digest(request.query.get("hub.verify_token", ""),
                                        self.config.get("verify_token", "") or "\0")):
            return HandshakeResponse(200, request.query.get("hub.challenge", ""), "text/plain")
        return HandshakeResponse(403)

    def verify(self, request: InboundRequest) -> bool:
        secret = self.config.get("app_secret", "")
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not secret or not sig.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig[7:])

    def parse(self, request: InboundRequest) -> ParseResult:
        payload = json.loads(request.body or b"{}")
        result = ParseResult()
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
                for m in value.get("messages", []):
                    result.envelopes.append(self._envelope(m, names))
                for st in value.get("statuses", []):
                    if st.get("status") in STATUS_MAP:
                        result.delivery_updates.append((st.get("id"), STATUS_MAP[st["status"]]))
        return result

    def _envelope(self, m, names):
        mtype = m.get("type")
        body, atts = "", []
        if mtype == "text":
            body = m.get("text", {}).get("body", "")
        elif mtype in ("image", "document", "audio", "video", "sticker"):
            media = m.get(mtype, {})
            body = media.get("caption", "") or f"[{mtype} received]"
            atts.append({"media_id": media.get("id"), "content_type": media.get("mime_type", ""),
                         "filename": media.get("filename") or f"{mtype}-{media.get('id')}"})
        elif mtype == "interactive":
            i = m.get("interactive", {})
            body = (i.get("button_reply") or i.get("list_reply") or {}).get("title", "")
        elif mtype == "button":
            body = m.get("button", {}).get("text", "")
        else:
            body = f"[{mtype} message]"
        reply_to = [m["context"]["id"]] if m.get("context", {}).get("id") else []
        return InboundEnvelope(
            external_id=m.get("id"), body=body, identity_kind="whatsapp", identity_value=m.get("from", ""),
            sender_name=names.get(m.get("from"), ""), in_reply_to=reply_to, attachments=atts,
        )

    def fetch_attachment(self, att):
        if "storage_path" in att:
            return super().fetch_attachment(att)
        h = {"Authorization": f"Bearer {self.config.get('access_token', '')}"}
        meta = requests.get(f"{self._base}/{att['media_id']}", headers=h, timeout=15)
        meta.raise_for_status()
        r = requests.get(meta.json()["url"], headers=h, timeout=60)
        r.raise_for_status()
        return r.content

    def _recipient(self, ticket):
        if ticket.requester and ticket.requester.phone:
            return ticket.requester.phone.lstrip("+")
        return (ticket.custom_fields.get("sender") or "").lstrip("+")

    def _in_window(self, session, ticket):
        last = session.scalars(select(Interaction.created_at).where(
            Interaction.ticket_id == ticket.id, Interaction.direction == "inbound", Interaction.source == "whatsapp")
            .order_by(Interaction.created_at.desc()).limit(1)).first()
        return bool(last and utcnow() - last < timedelta(hours=23, minutes=50))

    def _post(self, payload):
        r = requests.post(f"{self._base}/{self.config['phone_number_id']}/messages",
                          headers={"Authorization": f"Bearer {self.config.get('access_token', '')}"},
                          json=payload, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"WhatsApp API {r.status_code}: {r.text[:300]}")
        return r.json()["messages"][0]["id"]

    def send(self, session, ticket, interaction):
        to = self._recipient(ticket)
        if not to:
            raise ValueError("Requester has no WhatsApp number")
        text = f"{interaction.body}\n\n- {ticket.number}"
        if self._in_window(session, ticket):
            return self._post({"messaging_product": "whatsapp", "to": to, "type": "text",
                               "text": {"body": text[:4096], "preview_url": False}})
        if not self.config.get("template_name"):
            raise RuntimeError("Outside the 24h window and no template_name configured")
        return self._post({
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": self.config["template_name"],
                         "language": {"code": self.config.get("template_language", "en")},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": ticket.number},
                             {"type": "text", "text": interaction.body[:900]}]}]},
        })

    def acknowledge(self, session, ticket, envelope):
        if not self.settings.get("send_acknowledgement", True):
            return
        to = self._recipient(ticket)
        if to:
            self._post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {
                "body": f"Thanks - we've logged this as {ticket.number}. Reply here to add details or photos."}})
