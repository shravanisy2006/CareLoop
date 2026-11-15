"""Sends queued notifications and records exactly what happened to each.

* SENT only after Gmail accepted the message.
* FAILED with an attempt count when Gmail errors; retried on the next cycle
  until ``max_email_attempts`` is reached, then GAVE_UP.
* NOT_CONFIGURED when there are no email credentials (nothing is pretended).
* CANCELLED if a dose was resolved before its alert went out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from careloop.domain.models import (
    RESOLVABLE_DOSE_STATUSES,
    DoseStatus,
    Notification,
    NotificationKind,
    NotificationStatus,
)
from careloop.notifications.email_sender import (
    EmailDeliveryError,
    EmailNotConfigured,
    OutgoingEmail,
)
from careloop.services.context import ServiceContext

logger = logging.getLogger(__name__)

DOSE_KINDS = {
    NotificationKind.DOSE_REMINDER,
    NotificationKind.DOSE_ESCALATION_CARETAKER,
    NotificationKind.DOSE_ESCALATION_FAMILY,
}
BATCH_SIZE = 100


@dataclass
class DeliveryReport:
    sent: int = 0
    failed: int = 0
    gave_up: int = 0
    not_configured: int = 0
    cancelled: int = 0


class NotificationDelivery:
    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def _still_relevant(self, notification: Notification) -> bool:
        if notification.kind not in DOSE_KINDS:
            return True
        dose = self._ctx.repo.get_dose(notification.ref_key)
        if dose is None:
            return False
        if notification.kind is NotificationKind.DOSE_REMINDER:
            return dose.status is DoseStatus.SCHEDULED
        return dose.status in RESOLVABLE_DOSE_STATUSES

    def deliver_pending(self) -> DeliveryReport:
        repo, settings = self._ctx.repo, self._ctx.settings
        report = DeliveryReport()
        for item in repo.list_deliverable_notifications(settings.max_email_attempts, BATCH_SIZE):
            if not self._still_relevant(item):
                repo.update_notification_status(
                    item.id, NotificationStatus.CANCELLED, item.attempts, "", None
                )
                report.cancelled += 1
                continue
            if not item.recipient_email:
                repo.update_notification_status(
                    item.id, NotificationStatus.NO_EMAIL, item.attempts, "", None
                )
                continue
            try:
                self._ctx.email_sender.send(
                    OutgoingEmail(to=item.recipient_email, subject=item.subject, body=item.body)
                )
            except EmailNotConfigured:
                repo.update_notification_status(
                    item.id,
                    NotificationStatus.NOT_CONFIGURED,
                    item.attempts,
                    "Email is not configured on this server",
                    None,
                )
                report.not_configured += 1
                continue
            except EmailDeliveryError as exc:
                attempts = item.attempts + 1
                gave_up = attempts >= settings.max_email_attempts
                status = NotificationStatus.GAVE_UP if gave_up else NotificationStatus.FAILED
                repo.update_notification_status(item.id, status, attempts, str(exc), None)
                # Log ids and kinds only, never addresses or health details.
                logger.warning(
                    "Email delivery failed id=%s kind=%s attempt=%d",
                    item.id,
                    item.kind.value,
                    attempts,
                )
                if gave_up:
                    report.gave_up += 1
                else:
                    report.failed += 1
                continue
            repo.update_notification_status(
                item.id, NotificationStatus.SENT, item.attempts + 1, "", self._ctx.clock.now()
            )
            report.sent += 1
        return report
