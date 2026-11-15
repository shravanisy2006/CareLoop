"""Gmail SMTP sender: success, failures, and the unconfigured case.

smtplib.SMTP_SSL is replaced with a fake so no network is used. A real send
through Gmail needs credentials and is described in TESTING.md.
"""

from __future__ import annotations

import smtplib
from typing import ClassVar

import pytest

from careloop.notifications import email_sender
from careloop.notifications.email_sender import (
    EmailDeliveryError,
    EmailNotConfigured,
    GmailSmtpSender,
    OutgoingEmail,
    UnconfiguredSender,
    build_email_sender,
)

MESSAGE = OutgoingEmail(to="ravi@example.com", subject="Hello", body="Body text")


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []
    fail_login = False
    refuse = False

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials for ravi@example.com")
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)
        return {"ravi@example.com": (550, b"no")} if FakeSMTP.refuse else {}


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail_login, FakeSMTP.refuse = [], False, False
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", FakeSMTP)


def test_sends_over_implicit_tls_to_gmail():
    GmailSmtpSender("careloop@gmail.com", "app-password").send(MESSAGE)
    [smtp] = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.logged_in == ("careloop@gmail.com", "app-password")
    [sent] = smtp.sent
    assert sent["To"] == "ravi@example.com" and sent["Subject"] == "Hello"


def test_authentication_failure_is_reported_without_server_text():
    FakeSMTP.fail_login = True
    with pytest.raises(EmailDeliveryError) as err:
        GmailSmtpSender("careloop@gmail.com", "wrong").send(MESSAGE)
    assert "ravi@example.com" not in str(err.value)  # server reply text is not kept
    assert "SMTPAuthenticationError" in str(err.value)


def test_refused_recipient_is_a_failure():
    FakeSMTP.refuse = True
    with pytest.raises(EmailDeliveryError):
        GmailSmtpSender("careloop@gmail.com", "app-password").send(MESSAGE)


def test_missing_credentials_never_pretend_to_send():
    sender = build_email_sender(None, None)
    assert isinstance(sender, UnconfiguredSender)
    with pytest.raises(EmailNotConfigured):
        sender.send(MESSAGE)
    assert FakeSMTP.instances == []


def test_credentials_select_the_gmail_sender():
    assert isinstance(build_email_sender("a@gmail.com", "pw"), GmailSmtpSender)
