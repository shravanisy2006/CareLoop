"""The background care cycle that follows up every dose.

Each run:

1. creates dose records for the next 48 hours,
2. moves every unresolved, due dose one step up its escalation ladder
   (or closes it as UNCONFIRMED when its window is over),
3. queues reminders for appointments in the next 24 hours,
4. sends queued emails.

Every step is idempotent, so running the cycle twice (or in two processes)
cannot send the same email twice: escalation steps are conditional updates
and notification ids are deterministic.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from careloop.domain.escalation import (
    EscalationAction,
    plan_escalation,
    recipients_for_level,
)
from careloop.domain.models import SYSTEM_ACTOR_ROLE, AuditEvent, Circle, Dose, Member
from careloop.notifications.messages import appointment_notifications, dose_notifications
from careloop.services.context import ServiceContext
from careloop.services.dose_service import DoseService
from careloop.services.notification_delivery import DeliveryReport, NotificationDelivery

logger = logging.getLogger(__name__)

APPOINTMENT_REMINDER_LEAD = timedelta(hours=24)


@dataclass
class CycleReport:
    """What one run of the care cycle did, for logging and tests."""

    doses_created: int = 0
    escalations: int = 0
    closed_unconfirmed: int = 0
    appointment_reminders_queued: int = 0
    delivery: DeliveryReport = field(default_factory=DeliveryReport)
    skipped_overlapping_run: bool = False


def _system_audit(
    dose: Dose, action: str, now: datetime, before: dict[str, Any], after: dict[str, Any]
) -> AuditEvent:
    return AuditEvent(
        circle_id=dose.circle_id,
        actor_member_id=None,
        actor_role=SYSTEM_ACTOR_ROLE,
        action=action,
        entity_type="dose",
        entity_id=dose.id,
        created_at=now,
        before=before,
        after=after,
    )


class CareCycle:
    """Generates doses, escalates unanswered ones and sends queued email."""

    def __init__(self, ctx: ServiceContext, doses: DoseService) -> None:
        self._ctx = ctx
        self._doses = doses
        self._delivery = NotificationDelivery(ctx)
        self._lock = threading.Lock()

    def run_once(self) -> CycleReport:
        """Run one cycle, unless another run is already in progress in this process."""
        if not self._lock.acquire(blocking=False):
            return CycleReport(skipped_overlapping_run=True)
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self) -> CycleReport:
        repo, now = self._ctx.repo, self._ctx.clock.now()
        report = CycleReport()
        circles = {c.id: c for c in repo.list_circles()}
        members: dict[str, list[Member]] = {cid: repo.list_members(cid) for cid in circles}

        for circle in circles.values():
            report.doses_created += self._doses.generate_for_circle(circle, now)

        for dose in repo.list_open_doses_due_by(now):
            dose_circle = circles.get(dose.circle_id)
            if dose_circle is not None:
                self._escalate(dose_circle, dose, members[dose_circle.id], report, now)

        for appointment in repo.list_appointments_starting_between(
            now, now + APPOINTMENT_REMINDER_LEAD
        ):
            visit_circle = circles.get(appointment.circle_id)
            if visit_circle is None:
                continue
            report.appointment_reminders_queued += repo.enqueue_notifications(
                appointment_notifications(
                    visit_circle,
                    appointment,
                    members[visit_circle.id],
                    self._ctx.settings.public_url,
                    now,
                )
            )

        report.delivery = self._delivery.deliver_pending()
        logger.info(
            "Care cycle: created=%d escalated=%d closed=%d appt_reminders=%d sent=%d failed=%d",
            report.doses_created,
            report.escalations,
            report.closed_unconfirmed,
            report.appointment_reminders_queued,
            report.delivery.sent,
            report.delivery.failed,
        )
        return report

    def _escalate(
        self,
        circle: Circle,
        dose: Dose,
        members: list[Member],
        report: CycleReport,
        now: datetime,
    ) -> None:
        decision = plan_escalation(dose, circle.policy, now)
        before = {"status": dose.status.value, "escalation_level": int(dose.escalation_level)}

        if decision.action is EscalationAction.CLOSE:
            audit = _system_audit(
                dose,
                "dose.closed_unconfirmed",
                now,
                before,
                {"status": "unconfirmed", "note": "No answer was recorded; outcome unknown."},
            )
            if self._ctx.repo.close_dose_unconfirmed(dose.id, dose.escalation_level, audit):
                report.closed_unconfirmed += 1
            return

        if decision.action is EscalationAction.ADVANCE and decision.to_level is not None:
            medication = self._ctx.repo.get_medication(dose.medication_id)
            if medication is None:
                return
            recipients = recipients_for_level(decision.to_level, members)
            notifications = dose_notifications(
                circle,
                dose,
                medication,
                decision.to_level,
                recipients,
                self._ctx.settings.public_url,
                now,
            )
            audit = _system_audit(
                dose,
                "dose.escalated",
                now,
                before,
                {
                    "escalation_level": int(decision.to_level),
                    "notified_member_ids": [m.id for m in recipients],
                },
            )
            # Conditional: if the dose was confirmed after we read it, nothing
            # is written and no notification is queued.
            if self._ctx.repo.advance_escalation(
                dose.id, dose.escalation_level, decision.to_level, notifications, audit
            ):
                report.escalations += 1


class CareCycleRunner:
    """Runs the care cycle in a background thread every N seconds."""

    def __init__(self, cycle: CareCycle, interval_seconds: int) -> None:
        self._cycle = cycle
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background thread (does nothing if already running)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="careloop-cycle", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._cycle.run_once()
            except Exception:  # keep the scheduler alive; the next run retries
                logger.exception("Care cycle failed")
            self._stop.wait(self._interval)

    def stop(self) -> None:
        """Ask the thread to stop and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
