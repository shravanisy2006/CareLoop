"""Email delivery through Gmail's SMTP server.

``GmailSmtpSender`` sends real mail with a Google account and an app password
(requires 2-Step Verification on that account). When credentials are not set,
``UnconfiguredSender`` is used: it sends nothing and reports NOT_CONFIGURED,
so the notification history never claims an email was sent when it was not.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465  # implicit TLS


class EmailNotConfigured(Exception):
    """Raised when there are no email credentials. Not retried."""


class EmailDeliveryError(Exception):
    """Raised when the mail server did not accept the message. Retried."""


@dataclass(frozen=True)
class OutgoingEmail:
    """A plain-text email ready to send."""

    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    """Anything that can deliver an OutgoingEmail."""

    def send(self, message: OutgoingEmail) -> None:
        """Return only if the server accepted the message; otherwise raise."""
        ...


class GmailSmtpSender:
    """Sends mail through smtp.gmail.com with a Google App Password."""

    def __init__(self, address: str, app_password: str, timeout_seconds: float = 20.0) -> None:
        self._address = address
        self._app_password = app_password
        self._timeout = timeout_seconds

    def send(self, message: OutgoingEmail) -> None:
        """Send one message over implicit TLS; raise EmailDeliveryError on any failure."""
        email = EmailMessage()
        email["From"] = f"CareLoop <{self._address}>"
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=context, timeout=self._timeout
            ) as smtp:
                smtp.login(self._address, self._app_password)
                refused = smtp.send_message(email)
        except (smtplib.SMTPException, OSError) as exc:
            # Only the exception type is kept: server replies can echo addresses.
            raise EmailDeliveryError(f"Gmail SMTP error: {type(exc).__name__}") from exc
        if refused:
            raise EmailDeliveryError("Gmail refused the recipient address")


class UnconfiguredSender:
    """Used when no Gmail credentials are set; never pretends to send."""

    def send(self, message: OutgoingEmail) -> None:  # noqa: ARG002 - EmailSender signature
        """Always raise EmailNotConfigured."""
        raise EmailNotConfigured("CARELOOP_GMAIL_ADDRESS / CARELOOP_GMAIL_APP_PASSWORD not set")


def build_email_sender(address: str | None, app_password: str | None) -> EmailSender:
    """Return a Gmail sender if both credentials are set, else an UnconfiguredSender."""
    if address and app_password:
        return GmailSmtpSender(address, app_password)
    logger.warning("Email is not configured; notifications will be recorded as not_configured.")
    return UnconfiguredSender()
