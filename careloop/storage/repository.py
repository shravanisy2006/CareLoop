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
    # --- Care circles and members ---------------------------------------
    def create_circle(self, circle: Circle, elder: Member, audit: AuditEvent) -> None: ...
    def get_circle(self, circle_id: str) -> Circle | None: ...
    def get_circle_by_code(self, code: str) -> Circle | None: ...
    def list_circles(self) -> list[Circle]: ...
    def update_circle_policy(
        self, circle_id: str, policy: EscalationPolicy, audit: AuditEvent
    ) -> None: ...

    def add_member(self, member: Member, audit: AuditEvent) -> None:
        """Raises ValidationFailed if the name is already used in the circle."""
        ...

    def get_member(self, member_id: str) -> Member | None: ...
    def find_member_by_name(self, circle_id: str, name_key: str) -> Member | None: ...
    def list_members(self, circle_id: str) -> list[Member]: ...
    def record_login_attempt(
        self, member_id: str, failed_attempts: int, locked_until: datetime | None
    ) -> None: ...
    def deactivate_member(self, member_id: str, audit: AuditEvent) -> None: ...

    # --- Medications, appointments, proposals ---------------------------
    def get_medication(self, medication_id: str) -> Medication | None: ...
    def list_medications(self, circle_id: str) -> list[Medication]: ...
    def get_appointment(self, appointment_id: str) -> Appointment | None: ...
    def list_appointments(self, circle_id: str) -> list[Appointment]: ...
    def list_appointments_starting_between(
        self, start: datetime, end: datetime
    ) -> list[Appointment]: ...
    def get_proposal(self, proposal_id: str) -> Proposal | None: ...
    def list_proposals(
        self, circle_id: str, status: ProposalStatus | None = None
    ) -> list[Proposal]: ...

    def apply_changeset(self, changes: ChangeSet) -> None:
        """Apply everything atomically. Raises ConcurrencyConflict, and
        writes nothing, if any expected version/status no longer matches."""
        ...

    # --- Doses -----------------------------------------------------------
    def insert_doses_if_absent(self, doses: list[Dose]) -> int:
        """Insert doses whose id does not exist yet. Returns how many were new."""
        ...

    def get_dose(self, dose_id: str) -> Dose | None: ...
    def list_doses(self, circle_id: str, start: datetime, end: datetime) -> list[Dose]: ...
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
        """Move up one level only if the dose is still SCHEDULED at
        `from_level`. Queues the notifications in the same transaction."""
        ...

    def close_dose_unconfirmed(
        self, dose_id: str, from_level: EscalationLevel, audit: AuditEvent
    ) -> bool: ...

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
    ) -> None: ...

    def list_notifications(self, circle_id: str, limit: int = 100) -> list[Notification]: ...

    # --- Audit -----------------------------------------------------------
    def list_audit_events(self, circle_id: str, limit: int = 200) -> list[AuditEvent]: ...
