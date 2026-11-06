"""Generic JSON webhook channel for monitoring/alerting tools (Prometheus
Alertmanager, Grafana, Datadog, Azure Monitor, CloudWatch via SNS), MLOps
platforms (drift/eval alerts) or any system that can POST JSON.

Field mapping is configuration, not code, e.g. for Alertmanager:
{
  "auth": "hmac",                 # hmac | bearer
  "secret": "...",
  "items_path": "alerts",         # split payload into one envelope per item (optional)
  "map": {
    "subject": "annotations.summary",
    "body": "annotations.description",
    "external_id": "fingerprint",
    "fingerprint": "fingerprint",
    "status": "status",
    "severity": "labels.severity",
    "ci": "labels.service",
    "account_code": "labels.customer"
  },
  "severity_map": {"critical": [1, 1], "warning": [3, 2], "info": [4, 4]},   # -> [impact, urgency]
  "resolved_value": "resolved"
}
Repeated alerts with the same fingerprint are correlated into one open ticket.
"""
import hashlib
import hmac
import json
import re
import uuid

from ..base import ChannelAdapter, InboundEnvelope, InboundRequest, ParseResult

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def jpath(data, path, default=None):
    """Tiny path resolver: 'a.b[0].c' (a leading '$.' is optional)."""
    if not path:
        return default
    cur = data
    for name, idx in _TOKEN.findall(path.removeprefix("$.").removeprefix("$")):
        try:
            cur = cur[int(idx)] if idx else cur[name]
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    return cur


class GenericWebhookAdapter(ChannelAdapter):
    kind = "webhook"
    label = "Generic webhook / monitoring alerts"
    source = "webhook"
    supports_outbound = False
    config_help = {"auth": "hmac | bearer", "secret": "Shared secret", "items_path": "Optional list path",
                   "map": "Field mapping (see module docstring)", "severity_map": "severity -> [impact, urgency]"}

    def verify(self, request: InboundRequest) -> bool:
        secret = self.config.get("secret", "")
        if not secret:
            return False
        auth_mode = self.config.get("auth", "hmac")
        if auth_mode == "bearer":
            return hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {secret}")
        if auth_mode == "token":
            got = request.query.get("token") or request.headers.get("X-Webhook-Token", "")
            return bool(got) and hmac.compare_digest(secret, got)
        raw_sig = (request.headers.get("X-Signature", "") or request.headers.get("X-Hub-Signature-256", "")).strip()
        if not raw_sig:
            return False
        if raw_sig.lower().startswith("sha256="):
            raw_sig = raw_sig[7:].strip()
        expected = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.lower(), raw_sig.lower())

    def parse(self, request: InboundRequest) -> ParseResult:
        if len(request.body) > 1024 * 1024:
            raise ValueError("Payload exceeds 1MB limit")
        payload = json.loads(request.body or b"{}")
        items = jpath(payload, self.config["items_path"], []) if self.config.get("items_path") else [payload]
        if isinstance(items, dict):
            items = [items]
        return ParseResult([self.to_envelope(item, payload) for item in items])

    def to_envelope(self, item, root=None):
        m = self.config.get("map", {})
        get = lambda key: jpath(item, m.get(key)) if m.get(key) else None  # noqa: E731
        severity = str(get("severity") or "").lower()
        impact_urgency = self.config.get("severity_map", {}).get(severity)
        hints = {"tags": [t for t in ("alert", severity) if t]}
        if impact_urgency:
            hints["impact"], hints["urgency"] = impact_urgency
        if get("ci"):
            hints["ci"] = str(get("ci"))
        if get("account_code"):
            hints["account_code"] = str(get("account_code"))
        status = str(get("status") or "firing").lower()
        fingerprint = str(get("fingerprint") or "")
        ext = str(get("external_id") or uuid.uuid4().hex)
        if fingerprint:  # each firing/resolution is a distinct delivery of the same alert
            ext = f"{ext}:{status}:{jpath(item, m.get('timestamp', 'startsAt'), '') or uuid.uuid4().hex[:8]}"
        body = get("body") or json.dumps(item, indent=2)[:4000]
        return InboundEnvelope(
            external_id=ext, body=str(body), identity_kind="system", identity_value=self.channel.name,
            subject=str(get("subject") or f"Alert from {self.channel.name}"), fingerprint=fingerprint,
            alert_status="resolved" if status == self.config.get("resolved_value", "resolved") else "firing",
            hints=hints,
        )

    def send(self, session, ticket, interaction):
        raise NotImplementedError("Webhook channels are inbound only")
