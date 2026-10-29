"""Outbound SMTP."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from ..settings import settings

log = logging.getLogger(__name__)


def build_message(subject: str, body: str, recipients: list[str], *, from_email: str | None = None,
                  html: str | None = None, headers: dict[str, str] | None = None,
                  reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email or settings.default_from_email
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send_message(msg: EmailMessage) -> None:
    """Deliver via the configured SMTP relay. ``EMAIL_BACKEND=...console...`` logs instead (development)."""
    if settings.email_console:
        log.info("EMAIL to=%s subject=%s\n%s", msg["To"], msg["Subject"], msg.get_body(("plain",)).get_content())
        return
    with smtplib.SMTP(settings.email_host, settings.email_port, timeout=30) as smtp:
        if settings.email_use_tls:
            smtp.starttls()
        if settings.email_host_user:
            smtp.login(settings.email_host_user, settings.email_host_password)
        smtp.send_message(msg)


def send_mail(subject: str, body: str, recipients: list[str], **kwargs) -> None:
    send_message(build_message(subject, body, recipients, **kwargs))


def address_only(value: str) -> str:
    return parseaddr(value)[1]


def make_address(name: str, email: str) -> str:
    return formataddr((name, email))
