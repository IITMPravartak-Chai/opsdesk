"""Attachment intake: type and size limits, optional ClamAV scan, hashing and storage."""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import struct

from sqlalchemy.orm import Session

from ..db.models import Attachment
from ..db.models.tickets import attachment_path
from ..settings import settings
from ..storage import get_storage

log = logging.getLogger(__name__)


class AttachmentRejected(Exception):
    pass


def clamav_scan(data: bytes) -> str:
    """Stream bytes to clamd (INSTREAM). Returns 'clean' or 'infected'."""
    with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=30) as s:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 65536):
            chunk = data[i:i + 65536]
            s.sendall(struct.pack("!L", len(chunk)) + chunk)
        s.sendall(struct.pack("!L", 0))
        reply = s.recv(4096).decode(errors="ignore")
    return "infected" if "FOUND" in reply else "clean"


def store_attachment(session: Session, ticket, filename, content: bytes, content_type="", interaction=None,
                     user=None) -> Attachment:
    filename = os.path.basename(filename or "attachment")[:200] or "attachment"
    ext = os.path.splitext(filename)[1].lower()
    if ext in settings.attachment_blocked_extensions:
        raise AttachmentRejected(f"Files of type {ext} are not allowed")
    if len(content) > settings.attachment_max_mb * 1024 * 1024:
        raise AttachmentRejected(f"File exceeds {settings.attachment_max_mb} MB")
    status = "skipped"
    if settings.attachment_scanner == "clamav":
        try:
            status = clamav_scan(content)
        except OSError:
            log.exception("ClamAV unavailable; attachment held as pending")
            status = "pending"
        if status == "infected":
            raise AttachmentRejected("File failed malware scanning")
    ctype = content_type or "application/octet-stream"
    key = get_storage().save(attachment_path(ticket.tenant_id, ticket.id, filename), content, ctype)
    att = Attachment(tenant_id=ticket.tenant_id, ticket_id=ticket.id, interaction_id=getattr(interaction, "id", None),
                     file=key, filename=filename, content_type=ctype, size=len(content),
                     sha256=hashlib.sha256(content).hexdigest(), scan_status=status,
                     uploaded_by_id=getattr(user, "id", None))
    session.add(att)
    session.flush()
    return att
