"""Core domain entities for CareLoop.

These are plain dataclasses with no framework or database dependencies, so the
business rules that use them can be unit-tested in isolation.

All datetimes are timezone-aware and stored in UTC. Local times (for example
"08:00" for a dose) are interpreted in the care circle's timezone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum, IntEnum
from typing import Any


class Role(str, Enum):
    """Who a care-circle member is. The elder is the decision-maker."""

    ELDER = "elder"
    CARETAKER = "caretaker"
    FAMILY = "family"


class DoseStatus(str, Enum):
    """Lifecycle of one scheduled dose.

    SCHEDULED   - not yet resolved (it may be upcoming or already due)
    TAKEN       - someone recorded that the dose was taken
    SKIPPED     - someone recorded that the dose was deliberately not taken
    UNCONFIRMED - the follow-up window closed and nobody recorded an answer.
                  This means "unknown", not "missed".
    CANCELLED   - the medication schedule changed or stopped before this dose
    """

    SCHEDULED = "scheduled"
    TAKEN = "taken"
    SKIPPED = "skipped"
    UNCONFIRMED = "unconfirmed"
    CANCELLED = "cancelled"


#: Statuses a dose can be resolved *from*. A late answer to an unconfirmed
#: dose is accepted, because it makes the record more accurate.
RESOLVABLE_DOSE_STATUSES = frozenset({DoseStatus.SCHEDULED, DoseStatus.UNCONFIRMED})


class EscalationLevel(IntEnum):
    """How far follow-up on an unresolved dose has gone."""

    NONE = 0
    ELDER_REMINDED = 1
    CARETAKER_NOTIFIED = 2
    FAMILY_NOTIFIED = 3


class MedicationStatus(str, Enum):
    """Whether a medication is still being taken."""

    ACTIVE = "active"
    STOPPED = "stopped"


class AppointmentStatus(str, Enum):
    """Whether an appointment is still going ahead."""

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class ProposalKind(str, Enum):
    """The kinds of change a caretaker or family member can suggest."""

    ADD_MEDICATION = "add_medication"
    CHANGE_MEDICATION = "change_medication"
    STOP_MEDICATION = "stop_medication"
    ADD_APPOINTMENT = "add_appointment"
    CHANGE_APPOINTMENT = "change_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"


MEDICATION_PROPOSAL_KINDS = frozenset(
    {ProposalKind.ADD_MEDICATION, ProposalKind.CHANGE_MEDICATION, ProposalKind.STOP_MEDICATION}
)
APPOINTMENT_PROPOSAL_KINDS = frozenset(
    {ProposalKind.ADD_APPOINTMENT, ProposalKind.CHANGE_APPOINTMENT, ProposalKind.CANCEL_APPOINTMENT}
)


class AdviceSource(str, Enum):
    """Where a suggested change came from, shown to whoever decides on it."""

    DOCTOR = "doctor"
    PHARMACIST = "pharmacist"
    CARE_TEAM = "care_team"  # the caretaker's or family member's own idea


class ProposalStatus(str, Enum):
    """Where a suggested change stands."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class NotificationKind(str, Enum):
    """Why an email is being sent."""

    DOSE_REMINDER = "dose_reminder"
    DOSE_ESCALATION_CARETAKER = "dose_escalation_caretaker"
    DOSE_ESCALATION_FAMILY = "dose_escalation_family"
    APPOINTMENT_REMINDER = "appointment_reminder"
    PROPOSAL_PENDING = "proposal_pending"


class NotificationStatus(str, Enum):
    """Delivery state of one email.

    Nothing is marked SENT unless the mail server accepted the message.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"  # last attempt failed; may be retried
    GAVE_UP = "gave_up"  # failed the maximum number of times
    NOT_CONFIGURED = "not_configured"  # email credentials are not set up
    NO_EMAIL = "no_email"  # recipient has no email address on file
    CANCELLED = "cancelled"  # no longer relevant (e.g. dose was confirmed first)


@dataclass(frozen=True)
class EscalationPolicy:
    """Minutes after a dose's scheduled time at which each step happens.

    The elder is reminded at the scheduled time. These numbers are set by the
    care circle; CareLoop does not decide them on medical grounds.
    """

    caretaker_after_minutes: int = 30
    family_after_minutes: int = 60
    close_after_minutes: int = 180


@dataclass(frozen=True)
class Circle:
    """One elder's care circle: the elder plus the people helping them.

    `decision_maker_id` is the helper the elder has trusted to approve or
    decline changes on their behalf (for example when the elder is unwell).
    It is None unless the elder chooses someone, and the elder can remove it.
    """

    id: str
    code: str
    elder_name: str
    timezone: str
    policy: EscalationPolicy
    created_at: datetime
    decision_maker_id: str | None = None


@dataclass(frozen=True)
class Member:
    """A person in a care circle, with their role and sign-in state."""

    id: str
    circle_id: str
    display_name: str
    role: Role
    email: str | None
    passcode_hash: str
    failed_attempts: int
    locked_until: datetime | None
    active: bool
    created_at: datetime

    @property
    def name_key(self) -> str:
        """The member's name normalised for case- and space-insensitive matching."""
        return normalise_name(self.display_name)


@dataclass(frozen=True)
class MedicationDetails:
    """Fields a person enters about a medicine.

    CareLoop stores the dosage exactly as entered and never calculates or changes it.
    """

    name: str
    dosage: str
    instructions: str
    times: tuple[str, ...]  # local "HH:MM", sorted, unique
    start_date: date
    end_date: date | None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON-friendly values (for proposals and the audit log)."""
        return {
            "name": self.name,
            "dosage": self.dosage,
            "instructions": self.instructions,
            "times": list(self.times),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat() if self.end_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MedicationDetails:
        """Rebuild details from `to_dict` output."""
        return cls(
            name=data["name"],
            dosage=data["dosage"],
            instructions=data.get("instructions", ""),
            times=tuple(data["times"]),
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        )


@dataclass(frozen=True)
class Medication:
    """A medication in the elder's plan, versioned for safe concurrent edits."""

    id: str
    circle_id: str
    details: MedicationDetails
    status: MedicationStatus
    version: int
    created_at: datetime
    updated_at: datetime

    def to_audit_dict(self) -> dict[str, Any]:
        """Snapshot used as the before/after value in the audit log."""
        return {**self.details.to_dict(), "status": self.status.value, "version": self.version}


@dataclass(frozen=True)
class AppointmentDetails:
    """Fields a person enters about a doctor's appointment."""

    doctor_name: str
    purpose: str
    location: str
    starts_at: datetime  # UTC
    notes: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain JSON-friendly values (for proposals and the audit log)."""
        return {
            "doctor_name": self.doctor_name,
            "purpose": self.purpose,
            "location": self.location,
            "starts_at": self.starts_at.isoformat(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppointmentDetails:
        """Rebuild details from `to_dict` output."""
        return cls(
            doctor_name=data["doctor_name"],
            purpose=data["purpose"],
            location=data.get("location", ""),
            starts_at=datetime.fromisoformat(data["starts_at"]),
            notes=data.get("notes", ""),
        )


@dataclass(frozen=True)
class Appointment:
    """A doctor's appointment, versioned for safe concurrent edits."""

    id: str
    circle_id: str
    details: AppointmentDetails
    status: AppointmentStatus
    version: int
    created_at: datetime
    updated_at: datetime

    def to_audit_dict(self) -> dict[str, Any]:
        """Snapshot used as the before/after value in the audit log."""
        return {**self.details.to_dict(), "status": self.status.value, "version": self.version}


@dataclass(frozen=True)
class Dose:
    """One scheduled dose of one medication.

    Every dose has its own record so it can be followed until it is resolved.
    """

    id: str
    circle_id: str
    medication_id: str
    scheduled_for: datetime  # UTC
    status: DoseStatus
    escalation_level: EscalationLevel
    resolved_at: datetime | None
    resolved_by: str | None  # member id
    resolved_by_role: Role | None
    created_at: datetime


@dataclass(frozen=True)
class Advice:
    """Who advised a suggested change, and when."""

    source: AdviceSource
    advised_by: str = ""
    advised_on: date | None = None


@dataclass(frozen=True)
class Proposal:
    """A suggested change from a caretaker or family member.

    It changes nothing until the elder approves it.
    """

    id: str
    circle_id: str
    kind: ProposalKind
    target_id: str | None  # medication/appointment id for change/stop/cancel
    base_version: int | None  # version of the target when the proposal was made
    medication_details: MedicationDetails | None
    appointment_details: AppointmentDetails | None
    reason: str
    status: ProposalStatus
    proposed_by: str
    proposed_by_role: Role
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""
    advice: Advice = field(default_factory=lambda: Advice(AdviceSource.CARE_TEAM))

    def payload_dict(self) -> dict[str, Any]:
        """The proposed details as plain values (empty for stop/cancel)."""
        if self.medication_details is not None:
            return self.medication_details.to_dict()
        if self.appointment_details is not None:
            return self.appointment_details.to_dict()
        return {}


@dataclass(frozen=True)
class Notification:
    """One email to one person.

    The id is derived from (kind, ref_key, recipient) so the same notification can never be queued
    twice.
    """

    id: str
    circle_id: str
    kind: NotificationKind
    ref_key: str
    recipient_member_id: str
    recipient_email: str | None
    subject: str
    body: str
    status: NotificationStatus
    attempts: int
    last_error: str
    created_at: datetime
    sent_at: datetime | None = None


SYSTEM_ACTOR_ROLE = "system"


@dataclass(frozen=True)
class AuditEvent:
    """Append-only record of something that happened.

    Written by the backend only; there is no code path to edit or delete one.
    """

    circle_id: str
    actor_member_id: str | None  # None means the CareLoop scheduler
    actor_role: str  # a Role value or "system"
    action: str
    entity_type: str
    entity_id: str
    created_at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    id: str = ""


@dataclass
class ChangeSet:
    """Everything one user action changes, applied by the repository in a single transaction.

    Optimistic concurrency: each medication/appointment/proposal update carries
    the version or status the caller read. If the stored record no longer
    matches, the whole change set is rejected and nothing is written.
    """

    medications: list[tuple[Medication, int | None]] = field(default_factory=list)
    appointments: list[tuple[Appointment, int | None]] = field(default_factory=list)
    proposals: list[tuple[Proposal, ProposalStatus | None]] = field(default_factory=list)
    cancel_future_doses: list[tuple[str, datetime]] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)


def normalise_name(name: str) -> str:
    """Case- and whitespace-insensitive key used to match a member's name."""
    return " ".join(name.split()).casefold()


def next_version(entity: Medication | Appointment) -> int:
    """Version number an edited copy of ``entity`` must carry (optimistic concurrency)."""
    return entity.version + 1
