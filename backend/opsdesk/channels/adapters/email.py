"""Email channel.

* ``email`` (EmailWebhookAdapter): providers POST inbound mail to /api/channels/inbound/<token>/. Supports
  SendGrid Inbound Parse (multipart) and a generic JSON format (for SES->Lambda, Mailgun routes, Power Automate...).
* ``imap`` (ImapAdapter): polls a mailbox (Microsoft 365, Google Workspace) - the simplest option when customers
  already mail a shared support inbox.

Replies go out over SMTP/SES with the ticket number in the subject and In-Reply-To/References headers so the
customer's mail client keeps threading.
"""
from __future__ import annotations

import base64
import email
import email.policy
import hmac
import imaplib
import json
import logging
import re
import uuid
from email.utils import getaddresses, make_msgid, parseaddr

from sqlalchemy import select

from ...db.models import InboundMessage
from ...services import mailer
from ...settings import settings
from ...storage import get_storage
from ..base import ChannelAdapter, InboundEnvelope, InboundRequest, ParseResult

log = logging.getLogger(__name__)

QUOTE_MARKERS = [
    re.compile(r"^On .+wrote:\s*$", re.M),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^From: .+$\n^(Sent|Date): .+$", re.M),
    re.compile(r"^_{10,}\s*$", re.M),
]


def strip_quoted(text):
    """Keep only the new part of a reply."""
    cut = len(text)
    for rx in QUOTE_MARKERS:
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [ln for ln in text[:cut].splitlines() if not ln.startswith(">")]
    return "\n".join(lines).strip() or text.strip()


def html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html or "")
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _msgids(value):
    return re.findall(r"<[^>]+>", value or "")


def _stash(data, filename):
    name = (filename or "file").replace("/", "_").replace("\\", "_")
    return get_storage().save(f"inbound/{uuid.uuid4().hex}/{name}", data)


class EmailWebhookAdapter(ChannelAdapter):
    kind = "email"
    label = "Email (inbound webhook)"
    source = "email"
    config_help = {
        "provider": "sendgrid | generic_json",
        "webhook_token": "Shared secret; provider must send it as ?token=... or X-OpsDesk-Token header",
        "from_address": "Address replies are sent from, e.g. support@yourco.com",
        "reply_to": "Optional reply-to (the inbound address)",
    }

    def verify(self, request: InboundRequest) -> bool:
        expected = self.config.get("webhook_token", "")
        got = request.query.get("token") or request.headers.get("X-OpsDesk-Token", "")
        return bool(expected) and hmac.compare_digest(expected, got)

    def parse(self, request: InboundRequest) -> ParseResult:
        if self.config.get("provider", "sendgrid") == "generic_json":
            return ParseResult([self._from_json(json.loads(request.body or b"{}"))])
        return ParseResult([self._from_sendgrid(request)])

    def _from_json(self, d):
        sender_name, sender = parseaddr(d.get("from", ""))
        body = d.get("text") or html_to_text(d.get("html", ""))
        atts = []
        for a in d.get("attachments", []):  # {"filename","content_type","content_base64"}
            atts.append({"filename": a.get("filename"), "content_type": a.get("content_type", ""),
                         "storage_path": _stash(base64.b64decode(a.get("content_base64", "")), a.get("filename"))})
        return InboundEnvelope(
            external_id=d.get("message_id") or uuid.uuid4().hex, body=strip_quoted(body),
            identity_kind="email", identity_value=sender, sender_name=sender_name,
            subject=d.get("subject", ""),
            in_reply_to=_msgids(d.get("in_reply_to", "")) + _msgids(d.get("references", "")),
            attachments=atts,
        )

    def _from_sendgrid(self, request: InboundRequest):
        p = request.form
        sender_name, sender = parseaddr(p.get("from", ""))
        hdr = {}
        for line in p.get("headers", "").splitlines():
            k, _, v = line.partition(":")
            hdr[k.strip().lower()] = v.strip()
        body = p.get("text") or html_to_text(p.get("html", ""))
        atts = [{"filename": f.name, "content_type": f.content_type, "storage_path": _stash(f.data, f.name)}
                for f in request.files]
        return InboundEnvelope(
            external_id=hdr.get("message-id") or uuid.uuid4().hex, body=strip_quoted(body),
            identity_kind="email", identity_value=sender, sender_name=sender_name, subject=p.get("subject", ""),
            in_reply_to=_msgids(hdr.get("in-reply-to")) + _msgids(hdr.get("references")), attachments=atts,
        )

    # --- outbound ---
    def _thread_headers(self, session, ticket):
        last = session.scalars(select(InboundMessage).where(
            InboundMessage.ticket_id == ticket.id, InboundMessage.channel_id == self.channel.id)
            .order_by(InboundMessage.received_at.desc()).limit(1)).first()
        if last and last.external_id.startswith("<"):
            return {"In-Reply-To": last.external_id, "References": last.external_id}
        return {}

    def _send_mail(self, session, ticket, body, to):
        msg_id = make_msgid(domain=(self.config.get("from_address", "x@opsdesk.local").split("@")[-1]))
        headers = {"Message-ID": msg_id, **self._thread_headers(session, ticket)}
        if self.config.get("reply_to"):
            headers["Reply-To"] = self.config["reply_to"]
        mailer.send_mail(f"[{ticket.number}] {ticket.subject}", body, [to],
                         from_email=self.config.get("from_address") or settings.default_from_email, headers=headers)
        return msg_id

    def send(self, session, ticket, interaction):
        to = ticket.requester.email if ticket.requester else ticket.custom_fields.get("sender")
        if not to or to.endswith(".invalid"):
            raise ValueError("Requester has no email address")
        agent = interaction.author.display_name if interaction.author else "Support"
        body = (f"{interaction.body}\n\n-- \n{agent}\nReply to this email to update {ticket.number}. "
                f"Please keep [{ticket.number}] in the subject.")
        return self._send_mail(session, ticket, body, to)

    def acknowledge(self, session, ticket, envelope):
        if not self.settings.get("send_acknowledgement", True) or not ticket.requester:
            return
        if ticket.requester.email.endswith(".invalid"):
            return
        self._send_mail(session, ticket, (
            f"Hello{(' ' + ticket.requester.first_name) if ticket.requester.first_name else ''},\n\n"
            f"We've logged your request as {ticket.number} (priority {ticket.priority.key if ticket.priority else '-'}).\n"
            f"Reply to this email to add information.\n\nTrack it online: {settings.public_base_url}/tickets/{ticket.id}"),
            ticket.requester.email)


class ImapAdapter(EmailWebhookAdapter):
    kind = "imap"
    label = "Email (IMAP mailbox polling)"
    is_pull = True
    config_help = {
        "host": "imap.example.com", "port": "993", "username": "support@yourco.com",
        "password": "App password / OAuth token", "folder": "INBOX",
        "from_address": "support@yourco.com",
    }

    def verify(self, request: InboundRequest) -> bool:
        return False  # no webhook for pull channels

    def _mark_reauth(self, session):
        if session is not None:
            self.channel.config = {**self.channel.config, "oauth_status": "needs_reauthorization"}
            session.flush()

    def poll(self, session=None):
        c = self.config
        result = ParseResult()
        box = imaplib.IMAP4_SSL(c["host"], int(c.get("port", 993)))
        try:
            if c.get("auth_type") == "oauth2":
                from ..oauth import build_xoauth2_string, refresh_oauth_token
                if not refresh_oauth_token(session, self.channel):
                    log.warning("OAuth token refresh failed for channel %s; aborting poll cycle.", self.channel.id)
                    return result
                c = self.channel.config
                username = c.get("username", "")
                access_token = c.get("access_token", "")
                if not access_token:
                    log.warning("Channel %s has no valid access token. Marking needs_reauthorization.", self.channel.id)
                    self._mark_reauth(session)
                    return result
                auth_str = build_xoauth2_string(username, access_token)
                try:
                    box.authenticate("XOAUTH2", lambda x: auth_str.encode("utf-8"))
                except imaplib.IMAP4.error as err:
                    log.warning("XOAUTH2 IMAP authentication failed for channel %s: %s", self.channel.id, err)
                    self._mark_reauth(session)
                    return result
            else:
                box.login(c["username"], c["password"])

            box.select(c.get("folder", "INBOX"))
            _, data = box.search(None, "UNSEEN")
            for num in (data[0].split() if data and data[0] else [])[:50]:
                _, msg_data = box.fetch(num, "(RFC822)")
                result.envelopes.append(self.envelope_from_rfc822(msg_data[0][1]))
                box.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                box.logout()
            except Exception:  # noqa: BLE001
                pass
        return result

    @staticmethod
    def envelope_from_rfc822(raw: bytes):
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        sender_name, sender = getaddresses([msg.get("From", "")])[0]
        text_part = msg.get_body(preferencelist=("plain",))
        html_part = msg.get_body(preferencelist=("html",))
        body = text_part.get_content() if text_part else html_to_text(html_part.get_content() if html_part else "")
        atts = []
        for part in msg.iter_attachments():
            payload = part.get_payload(decode=True) or b""
            atts.append({"filename": part.get_filename(), "content_type": part.get_content_type(),
                         "storage_path": _stash(payload, part.get_filename())})
        return InboundEnvelope(
            external_id=msg.get("Message-ID") or uuid.uuid4().hex, body=strip_quoted(body),
            identity_kind="email", identity_value=sender, sender_name=sender_name,
            subject=str(msg.get("Subject", "")),
            in_reply_to=_msgids(msg.get("In-Reply-To")) + _msgids(msg.get("References")), attachments=atts,
        )
