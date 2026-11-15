"""Dose records: generating them from schedules, confirming them, and
reporting adherence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from careloop.domain.adherence import AdherenceSummary, summarise_adherence
from careloop.domain.changes import audit_event
from careloop.domain.errors import (
    AlreadyResolved,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
)
from careloop.domain.models import (
    RESOLVABLE_DOSE_STATUSES,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    Medication,
    MedicationStatus,
    Member,
    Role,
)
from careloop.domain.permissions import Action, authorize, can
from careloop.domain.schedule import dose_id_for, dose_times_in_window
from careloop.services.context import ServiceContext

GENERATION_HORIZON = timedelta(hours=48)
GENERATION_LOOKBACK = timedelta(hours=24)
#: A dose can be recorded up to an hour before its time (e.g. taken with breakfast).
EARLY_CONFIRMATION_ALLOWANCE = timedelta(hours=1)


@dataclass(frozen=True)
class DoseView:
    """A dose together with the medicine it belongs to, for display."""

    dose: Dose
    medication: Medication

    @property
    def is_open(self) -> bool:
        return self.dose.status in RESOLVABLE_DOSE_STATUSES


class DoseService:
    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    # --- Generation ------------------------------------------------------

    def generate_for_medication(self, circle: Circle, medication: Medication, now: datetime) -> int:
        """Create dose records for the next 48 hours (idempotent).

        Doses are never back-dated before the medication was added or last
        changed, so a new medicine does not start life with "unconfirmed"
        doses nobody could have taken.
        """
        if medication.status is not MedicationStatus.ACTIVE:
            return 0
        start = max(now - GENERATION_LOOKBACK, medication.updated_at)
        times = dose_times_in_window(
            medication.details, circle.timezone, start, now + GENERATION_HORIZON
        )
        doses = [
            Dose(
                id=dose_id_for(medication.id, medication.version, t),
                circle_id=circle.id,
                medication_id=medication.id,
                scheduled_for=t,
                status=DoseStatus.SCHEDULED,
                escalation_level=EscalationLevel.NONE,
                resolved_at=None,
                resolved_by=None,
                resolved_by_role=None,
                created_at=now,
            )
            for t in times
        ]
        return self._ctx.repo.insert_doses_if_absent(doses)

    def generate_for_circle(self, circle: Circle, now: datetime) -> int:
        return sum(
            self.generate_for_medication(circle, med, now)
            for med in self._ctx.repo.list_medications(circle.id)
        )

    # --- Reading -----------------------------------------------------------

    def _views(self, circle_id: str, doses: list[Dose]) -> list[DoseView]:
        meds = {m.id: m for m in self._ctx.repo.list_medications(circle_id)}
        return [DoseView(d, meds[d.medication_id]) for d in doses if d.medication_id in meds]

    def today(self, viewer: Member, circle: Circle) -> list[DoseView]:
        """Today's doses (local day), plus any earlier ones still awaiting an answer."""
        authorize(viewer, Action.VIEW_CIRCLE, circle.id)
        now = self._ctx.clock.now()
        tz = ZoneInfo(circle.timezone)
        local_midnight = datetime.combine(now.astimezone(tz).date(), datetime.min.time(), tz)
        start = local_midnight.astimezone(timezone.utc)
        doses = self._ctx.repo.list_doses(
            circle.id, start - GENERATION_LOOKBACK, start + timedelta(days=1)
        )
        relevant = [
            d
            for d in doses
            if d.status is not DoseStatus.CANCELLED
            and (d.scheduled_for >= start or d.status is DoseStatus.SCHEDULED)
        ]
        return self._views(circle.id, relevant)

    def history(self, viewer: Member, circle: Circle, days: int = 7) -> list[DoseView]:
        authorize(viewer, Action.VIEW_CIRCLE, circle.id)
        now = self._ctx.clock.now()
        doses = self._ctx.repo.list_doses(
            circle.id, now - timedelta(days=days), now + timedelta(minutes=1)
        )
        return list(reversed(self._views(circle.id, doses)))

    def adherence(self, viewer: Member, circle: Circle, days: int = 7) -> AdherenceSummary:
        authorize(viewer, Action.VIEW_CIRCLE, circle.id)
        now = self._ctx.clock.now()
        doses = self._ctx.repo.list_doses(
            circle.id, now - timedelta(days=days), now + timedelta(minutes=1)
        )
        return summarise_adherence(doses, now, circle.policy.caretaker_after_minutes)

    # --- Recording an answer -----------------------------------------------

    def record(self, actor: Member, dose_id: str, outcome: str) -> Dose:
        """Record that a dose was taken or skipped.

        The elder records their own doses. A caretaker can record on the
        elder's behalf (for example after checking in); the record shows who
        did it. Family members can only view.
        """
        try:
            status = DoseStatus(outcome)
        except ValueError:
            raise InvalidStateTransition("Please choose taken or skipped.") from None
        if status not in (DoseStatus.TAKEN, DoseStatus.SKIPPED):
            raise InvalidStateTransition("Please choose taken or skipped.")

        dose = self._ctx.repo.get_dose(dose_id)
        if dose is None or dose.circle_id != actor.circle_id:
            raise NotFound()
        action = (
            Action.CONFIRM_OWN_DOSE if actor.role is Role.ELDER else Action.RECORD_DOSE_ON_BEHALF
        )
        if not can(actor, action):
            raise PermissionDenied("Only the elder or a caretaker can record a dose.")
        authorize(actor, action, dose.circle_id)

        now = self._ctx.clock.now()
        if dose.status not in RESOLVABLE_DOSE_STATUSES:
            raise AlreadyResolved()
        if dose.scheduled_for - now > EARLY_CONFIRMATION_ALLOWANCE:
            raise InvalidStateTransition("This dose is not due yet.")

        audit = audit_event(
            actor,
            f"dose.{status.value}",
            "dose",
            dose.id,
            now,
            before={"status": dose.status.value, "escalation_level": int(dose.escalation_level)},
            after={
                "status": status.value,
                "recorded_late": dose.status is DoseStatus.UNCONFIRMED,
                "on_behalf_of_elder": actor.role is not Role.ELDER,
            },
        )
        if not self._ctx.repo.resolve_dose(dose.id, status, actor.id, actor.role, now, audit):
            # Someone else recorded it between our read and our write.
            raise AlreadyResolved()
        resolved = self._ctx.repo.get_dose(dose.id)
        assert resolved is not None
        return resolved
