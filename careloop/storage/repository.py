"""The storage contract every backend implements.

Two implementations exist:

* ``SqliteRepository`` - the default; zero setup, used by the test suite.
* ``FirestoreRepository`` - Google Cloud Firestore, selected with
  ``CARELOOP_STORAGE=firestore``.

Methods that change state are *conditional*: they only write if the record is
still in the state the caller expects, and they report whether they did. This
is what makes dose confirmation, escalation and approvals safe when two things
happen at the same moment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from careloop.domain.models import (
    Appointment,
    AuditEvent,
    ChangeSet,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Medication,
    Member,
    Notification,
    NotificationStatus,
    Proposal,
    ProposalStatus,
    Role,
)


class Repository(Protocol):
    """Storage operations used by the services, implemented per backend."""

    # --- Care circles and members ---------------------------------------
    def create_circle(self, circle: Circle, elder: Member, audit: AuditEvent) -> None:
        """Store a new circle, its elder and the audit event together."""
        ...

    def get_circle(self, circle_id: str) -> Circle | None:
        """Return a circle by id, or None."""

    def get_circle_by_code(self, code: str) -> Circle | None:
        """Return a circle by its sign-in code, or None."""

    def list_circles(self) -> list[Circle]:
        """Return every circle (used by the care cycle)."""

    def update_circle_policy(
        self, circle_id: str, policy: EscalationPolicy, audit: AuditEvent
    ) -> None:
        """Save new escalation timings with an audit event."""

    def update_decision_maker(
        self, circle_id: str, member_id: str | None, audit: AuditEvent
    ) -> None:
        """Set (or clear, with None) the elder's trusted decision-maker."""
        ...

    def add_member(self, member: Member, audit: AuditEvent) -> None:
        """Raises ValidationFailed if the name is already used in the circle."""
        ...

    def get_member(self, member_id: str) -> Member | None:
        """Return a member by id, or None."""

    def find_member_by_name(self, circle_id: str, name_key: str) -> Member | None:
        """Return the member with this normalised name in a circle, or None."""

    def list_members(self, circle_id: str) -> list[Member]:
        """Return a circle's members, oldest first, including removed ones."""

    def record_login_attempt(
        self, member_id: str, failed_attempts: int, locked_until: datetime | None
    ) -> None:
        """Save the failed-attempt count and lockout time after a sign-in."""

    def deactivate_member(self, member_id: str, audit: AuditEvent) -> None:
        """Remove a member's access with an audit event; their history stays."""

    # --- Medications, appointments, proposals ---------------------------
    def get_medication(self, medication_id: str) -> Medication | None:
        """Return a medication by id, or None."""

    def list_medications(self, circle_id: str) -> list[Medication]:
        """Return a circle's medications."""

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        """Return an appointment by id, or None."""

    def list_appointments(self, circle_id: str) -> list[Appointment]:
        """Return a circle's appointments, earliest first."""

    def list_appointments_starting_between(
        self, start: datetime, end: datetime
    ) -> list[Appointment]:
        """Return scheduled appointments in [start, end), across circles."""

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        """Return a proposal by id, or None."""

    def list_proposals(
        self, circle_id: str, status: ProposalStatus | None = None
    ) -> list[Proposal]:
        """Return a circle's proposals, newest first, optionally by status."""

    def apply_changeset(self, changes: ChangeSet) -> None:
        """Apply everything atomically.

        Raises ConcurrencyConflict, and writes nothing, if any expected version/status no longer
        matches.
        """
        ...

    # --- Doses -----------------------------------------------------------
    def insert_doses_if_absent(self, doses: list[Dose]) -> int:
        """Insert doses whose id does not exist yet. Returns how many were new."""
        ...

    def get_dose(self, dose_id: str) -> Dose | None:
        """Return a dose by id, or None."""

    def list_doses(self, circle_id: str, start: datetime, end: datetime) -> list[Dose]:
        """Return a circle's doses scheduled in [start, end), earliest first."""

    def list_open_doses_due_by(self, now: datetime) -> list[Dose]:
        """All SCHEDULED doses whose time has come, across every circle."""
        ...

    def resolve_dose(
        self,
        dose_id: str,
        status: DoseStatus,
        by_member: str,
        by_role: Role,
        at: datetime,
        audit: AuditEvent,
    ) -> bool:
        """Record taken/skipped only if the dose is still resolvable."""
        ...

    def advance_escalation(
        self,
        dose_id: str,
        from_level: EscalationLevel,
        to_level: EscalationLevel,
        notifications: list[Notification],
        audit: AuditEvent,
    ) -> bool:
        """Move up one level only if the dose is still SCHEDULED at `from_level`.

        Queues the notifications in the same transaction.
        """
        ...

    def close_dose_unconfirmed(
        self, dose_id: str, from_level: EscalationLevel, audit: AuditEvent
    ) -> bool:
        """Mark a dose UNCONFIRMED only if still SCHEDULED at `from_level`."""

    # --- Notifications ---------------------------------------------------
    def enqueue_notifications(self, notifications: list[Notification]) -> int:
        """Insert notifications whose id is new. Returns how many were new."""
        ...

    def list_deliverable_notifications(self, max_attempts: int, limit: int) -> list[Notification]:
        """PENDING notifications plus FAILED ones with attempts left."""
        ...

    def update_notification_status(
        self,
        notification_id: str,
        status: NotificationStatus,
        attempts: int,
        last_error: str,
        sent_at: datetime | None,
    ) -> None:
        """Record a delivery outcome, unless the email was already cancelled."""

    def list_notifications(self, circle_id: str, limit: int = 100) -> list[Notification]:
        """Return a circle's emails, newest first."""

    # --- Audit -----------------------------------------------------------
    def list_audit_events(self, circle_id: str, limit: int = 200) -> list[AuditEvent]:
        """Return a circle's audit events, newest first."""
