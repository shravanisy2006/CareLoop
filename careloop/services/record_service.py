"""Medications and appointments.

Direct changes are for the elder only. Everyone else goes through
ProposalService, and their suggestion changes nothing until the elder
approves it.
"""

from __future__ import annotations

from careloop.domain import changes
from careloop.domain.errors import NotFound
from careloop.domain.models import (
    Appointment,
    AppointmentDetails,
    ChangeSet,
    Medication,
    MedicationDetails,
    Member,
)
from careloop.domain.permissions import Action, authorize
from careloop.services.context import ServiceContext
from careloop.services.dose_service import DoseService


class RecordService:
    """Medications and appointments: reading for everyone, direct edits for the elder."""

    def __init__(self, ctx: ServiceContext, doses: DoseService) -> None:
        self._ctx = ctx
        self._doses = doses

    # --- Reading -----------------------------------------------------------

    def get_medication(self, viewer: Member, medication_id: str) -> Medication:
        """Return one medication from the viewer's circle."""
        medication = self._ctx.repo.get_medication(medication_id)
        if medication is None or medication.circle_id != viewer.circle_id:
            raise NotFound()
        authorize(viewer, Action.VIEW_CIRCLE, medication.circle_id)
        return medication

    def list_medications(self, viewer: Member) -> list[Medication]:
        """Return all medications in the viewer's circle."""
        authorize(viewer, Action.VIEW_CIRCLE, viewer.circle_id)
        return self._ctx.repo.list_medications(viewer.circle_id)

    def get_appointment(self, viewer: Member, appointment_id: str) -> Appointment:
        """Return one appointment from the viewer's circle."""
        appointment = self._ctx.repo.get_appointment(appointment_id)
        if appointment is None or appointment.circle_id != viewer.circle_id:
            raise NotFound()
        authorize(viewer, Action.VIEW_CIRCLE, appointment.circle_id)
        return appointment

    def list_appointments(self, viewer: Member) -> list[Appointment]:
        """Return all appointments in the viewer's circle, earliest first."""
        authorize(viewer, Action.VIEW_CIRCLE, viewer.circle_id)
        return self._ctx.repo.list_appointments(viewer.circle_id)

    # --- Elder's direct changes -------------------------------------------

    def _apply_and_refresh_doses(self, elder: Member, change_set: ChangeSet) -> None:
        self._ctx.repo.apply_changeset(change_set)
        circle = self._ctx.repo.get_circle(elder.circle_id)
        if circle is None:
            raise NotFound()
        for medication, _ in change_set.medications:
            saved = self._ctx.repo.get_medication(medication.id)
            if saved is not None:
                self._doses.generate_for_medication(circle, saved, self._ctx.clock.now())

    def add_medication(self, elder: Member, details: MedicationDetails) -> str:
        """Add a medication (elder only) and create its upcoming doses; returns its id."""
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, elder.circle_id)
        new_id = self._ctx.id_factory()
        self._apply_and_refresh_doses(
            elder, changes.add_medication(elder, details, self._ctx.clock.now(), new_id)
        )
        return new_id

    def change_medication(
        self, elder: Member, medication_id: str, details: MedicationDetails
    ) -> None:
        """Change a medication (elder only); upcoming doses follow the new details."""
        current = self.get_medication(elder, medication_id)
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, current.circle_id)
        self._apply_and_refresh_doses(
            elder, changes.change_medication(elder, current, details, self._ctx.clock.now())
        )

    def stop_medication(self, elder: Member, medication_id: str) -> None:
        """Stop a medication (elder only) and cancel its upcoming doses."""
        current = self.get_medication(elder, medication_id)
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, current.circle_id)
        self._ctx.repo.apply_changeset(
            changes.stop_medication(elder, current, self._ctx.clock.now())
        )

    def add_appointment(self, elder: Member, details: AppointmentDetails) -> str:
        """Add an appointment (elder only); returns its id."""
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, elder.circle_id)
        new_id = self._ctx.id_factory()
        self._ctx.repo.apply_changeset(
            changes.add_appointment(elder, details, self._ctx.clock.now(), new_id)
        )
        return new_id

    def change_appointment(
        self, elder: Member, appointment_id: str, details: AppointmentDetails
    ) -> None:
        """Change an appointment (elder only)."""
        current = self.get_appointment(elder, appointment_id)
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, current.circle_id)
        self._ctx.repo.apply_changeset(
            changes.change_appointment(elder, current, details, self._ctx.clock.now())
        )

    def cancel_appointment(self, elder: Member, appointment_id: str) -> None:
        """Cancel an appointment (elder only)."""
        current = self.get_appointment(elder, appointment_id)
        authorize(elder, Action.EDIT_RECORDS_DIRECTLY, current.circle_id)
        self._ctx.repo.apply_changeset(
            changes.cancel_appointment(elder, current, self._ctx.clock.now())
        )
