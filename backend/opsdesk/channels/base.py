"""Channel adapter contract. To add a channel (Teams, Slack, Telegram, SMS, a partner's ITSM...), subclass
ChannelAdapter, implement parse() and send(), and register the class in ``CHANNEL_ADAPTERS`` (or
``EXTRA_CHANNEL_ADAPTERS``). Nothing else changes: the router, ticket engine, SLA and AI work the same for
every channel.

Adapters are framework neutral: they receive an ``InboundRequest`` (a plain snapshot of the HTTP request) and
answer provider handshakes with a ``HandshakeResponse``."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping

from ..storage import get_storage


class _Headers(dict):
    """Case-insensitive header lookup."""

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        super().__init__({k.lower(): v for k, v in (items or {}).items()})

    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key.lower(), default)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __contains__(self, key) -> bool:  # type: ignore[override]
        return super().__contains__(key.lower())


@dataclass
class UploadedFile:
    name: str
    content_type: str
    data: bytes


@dataclass
class InboundRequest:
    """Plain snapshot of a webhook call."""
    method: str = "POST"
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    form: Mapping[str, str] = field(default_factory=dict)       # multipart/urlencoded fields (SendGrid)
    files: list = field(default_factory=list)                    # [UploadedFile]

    def __post_init__(self) -> None:
        self.headers = _Headers(self.headers)


@dataclass
class HandshakeResponse:
    status: int = 200
    body: str = ""
    content_type: str = "text/plain"


@dataclass
class InboundEnvelope:
    external_id: str
    body: str
    identity_kind: str = "email"          # email | whatsapp | system | <custom>
    identity_value: str = ""              # address / phone / system id
    sender_name: str = ""
    subject: str = ""
    in_reply_to: list = field(default_factory=list)   # external ids this message replies to
    ticket_ref: str = ""                  # e.g. INC-000123 parsed from subject
    attachments: list = field(default_factory=list)   # [{"filename","content_type","storage_path"|"media_id"}]
    fingerprint: str = ""                 # alert correlation key (monitoring channels)
    alert_status: str = ""                # firing | resolved
    hints: dict = field(default_factory=dict)  # impact, urgency, account_code, ticket_type, tags, ci

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class ParseResult:
    envelopes: list = field(default_factory=list)
    delivery_updates: list = field(default_factory=list)  # [(external_id, status)]


class ChannelAdapter:
    kind = ""
    label = ""
    source = "other"              # value stored on Ticket.source
    supports_outbound = True
    is_pull = False               # pull-based (polled) instead of webhook
    config_help: dict = {}        # documented in the console: key -> description

    def __init__(self, channel):
        self.channel = channel
        self.config = channel.config or {}
        self.settings = channel.settings or {}

    # --- inbound (webhook) ---
    def handshake(self, request: InboundRequest) -> HandshakeResponse | None:
        """Answer provider verification requests. Return a HandshakeResponse or None."""
        return None

    def verify(self, request: InboundRequest) -> bool:
        """Authenticate the webhook call (signature / token)."""
        return False

    def parse(self, request: InboundRequest) -> ParseResult:
        raise NotImplementedError

    # --- inbound (polling) ---
    def poll(self, session=None) -> ParseResult:
        return ParseResult()

    def fetch_attachment(self, att) -> bytes:
        return get_storage().read(att["storage_path"])

    # --- outbound ---
    def send(self, session, ticket, interaction) -> str:
        """Deliver an agent's public reply. Return the provider message id."""
        raise NotImplementedError

    def acknowledge(self, session, ticket, envelope):
        """Optional auto-acknowledgement for a newly created ticket."""
        return None
