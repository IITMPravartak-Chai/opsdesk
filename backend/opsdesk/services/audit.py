"""Append-only audit trail (the table is protected by the ``audit_immutable`` trigger)."""
from __future__ import annotations

import ipaddress

from sqlalchemy.orm import Session

from ..db.models import AuditLog


def client_ip(request) -> str | None:
    """First X-Forwarded-For hop, else the peer address; None if it is not a valid IP literal.

    ``request`` is a Starlette/FastAPI ``Request`` (or anything with ``headers`` and ``client``)."""
    if request is None:
        return None
    raw = ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        raw = (headers.get("x-forwarded-for", "") or "").split(",")[0].strip()
    if not raw:
        client = getattr(request, "client", None)
        raw = getattr(client, "host", "") or ""
    try:
        return str(ipaddress.ip_address(raw)) if raw else None
    except ValueError:
        return None


def audit(session: Session, tenant_id: int, action: str, obj=None, actor=None, changes: dict | None = None,
          request=None) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=getattr(actor, "id", None) if actor is not None else None,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "id", "") or "") if obj is not None else "",
        changes=changes or {},
        ip_address=client_ip(request),
    )
    session.add(entry)
    return entry
