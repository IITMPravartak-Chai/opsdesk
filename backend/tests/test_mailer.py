"""Outbound mail is well formed: receivers such as Outlook / Microsoft 365 junk or refuse mail without a Date or
a Message-ID, and smtplib adds neither."""
from email.utils import parsedate_to_datetime

from opsdesk.services import mailer


def test_every_message_carries_a_date_and_a_message_id_on_the_senders_domain():
    msg = mailer.build_message("Your OpsDesk sign-in code", "123456", ["someone@outlook.com"],
                               from_email="OpsDesk <support@help.example.org>")
    assert parsedate_to_datetime(msg["Date"]).tzinfo is not None
    assert msg["Message-ID"].startswith("<") and msg["Message-ID"].endswith("@help.example.org>")
    assert msg["To"] == "someone@outlook.com" and msg["From"] == "OpsDesk <support@help.example.org>"


def test_two_messages_never_share_a_message_id():
    a, b = (mailer.build_message("s", "b", ["x@outlook.com"]) for _ in range(2))
    assert a["Message-ID"] != b["Message-ID"]


def test_a_message_id_given_by_the_caller_is_kept():
    """Channel replies set their own id so the customer's answer threads back onto the ticket."""
    msg = mailer.build_message("s", "b", ["x@outlook.com"], headers={"Message-ID": "<thread-1@support.example.org>"})
    assert msg.get_all("Message-ID") == ["<thread-1@support.example.org>"] and msg.get_all("Date") and len(msg.get_all("Date")) == 1
