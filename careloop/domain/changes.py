"""Pure builders that describe a change as a ChangeSet.

The same builders are used when the elder edits a record directly and when
the elder approves someone else's proposal, so there is exactly one place
that decides what "change a medication" means. The repository then applies
the ChangeSet atomically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from careloop.domain.errors import ConcurrencyConflict, InvalidStateTransition
from careloop.domain.models import (
    Appointment,
    AppointmentDetails,
    AppointmentStatus,
    AuditEvent,
    ChangeSet,
    Medication,
    MedicationDetails,
    MedicationStatus,
    Member,
    Proposal,
    ProposalKind,
    ProposalStatus,
    Role,
    next_version,
)


def audit_event(
    actor: Member,
    action: str,
    entity_type: str,
    entity_id: str,
    now: datetime,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    """Build an audit event for an action taken by `actor`."""
    return AuditEvent(
        circle_id=actor.circle_id,
        actor_member_id=actor.id,
        actor_role=actor.role.value,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        created_at=now,
        before=before,
        after=after,
    )


# --- Medications ---------------------------------------------------------


def add_medication(
    actor: Member, details: MedicationDetails, now: datetime, new_id: str
) -> ChangeSet:
    """Describe adding a new, active medication."""
    medication = Medication(
        id=new_id,
        circle_id=actor.circle_id,
        details=details,
        status=MedicationStatus.ACTIVE,
        version=1,
        created_at=now,
        updated_at=now,
    )
    return ChangeSet(
        medications=[(medication, None)],
        audit_events=[
            audit_event(
                actor,
                "medication.added",
                "medication",
                new_id,
                now,
                after=medication.to_audit_dict(),
            )
        ],
    )


def change_medication(
    actor: Member, current: Medication, details: MedicationDetails, now: datetime
) -> ChangeSet:
    """Describe changing an active medication's details."""
    if current.status is not MedicationStatus.ACTIVE:
        raise InvalidStateTransition("This medicine has been stopped and cannot be changed.")
    updated = replace(current, details=details, updated_at=now, version=next_version(current))
    # Future doses are cancelled and regenerated from the new details, so no
    # upcoming dose ever reflects an outdated schedule.
    return ChangeSet(
        medications=[(updated, current.version)],
        cancel_future_doses=[(current.id, now)],
        audit_events=[
            audit_event(
                actor,
                "medication.changed",
                "medication",
                current.id,
                now,
                before=current.to_audit_dict(),
                after=updated.to_audit_dict(),
            )
        ],
    )


def stop_medication(actor: Member, current: Medication, now: datetime) -> ChangeSet:
    """Describe stopping a medication and cancelling its upcoming doses."""
    if current.status is not MedicationStatus.ACTIVE:
        raise InvalidStateTransition("This medicine is already stopped.")
    updated = replace(
        current, status=MedicationStatus.STOPPED, updated_at=now, version=next_version(current)
    )
    return ChangeSet(
        medications=[(updated, current.version)],
        cancel_future_doses=[(current.id, now)],
        audit_events=[
            audit_event(
                actor,
                "medication.stopped",
                "medication",
                current.id,
                now,
                before=current.to_audit_dict(),
                after=updated.to_audit_dict(),
            )
        ],
    )


# --- Appointments --------------------------------------------------------


def add_appointment(
    actor: Member, details: AppointmentDetails, now: datetime, new_id: str
) -> ChangeSet:
    """Describe adding a new appointment."""
    appointment = Appointment(
        id=new_id,
        circle_id=actor.circle_id,
        details=details,
        status=AppointmentStatus.SCHEDULED,
        version=1,
        created_at=now,
        updated_at=now,
    )
    return ChangeSet(
        appointments=[(appointment, None)],
        audit_events=[
            audit_event(
                actor,
                "appointment.added",
                "appointment",
                new_id,
                now,
                after=appointment.to_audit_dict(),
            )
        ],
    )


def change_appointment(
    actor: Member, current: Appointment, details: AppointmentDetails, now: datetime
) -> ChangeSet:
    """Describe changing a scheduled appointment."""
    if current.status is not AppointmentStatus.SCHEDULED:
        raise InvalidStateTransition("This appointment was cancelled and cannot be changed.")
    updated = replace(current, details=details, updated_at=now, version=next_version(current))
    return ChangeSet(
        appointments=[(updated, current.version)],
        audit_events=[
            audit_event(
                actor,
                "appointment.changed",
                "appointment",
                current.id,
                now,
                before=current.to_audit_dict(),
                after=updated.to_audit_dict(),
            )
        ],
    )


def cancel_appointment(actor: Member, current: Appointment, now: datetime) -> ChangeSet:
    """Describe cancelling a scheduled appointment."""
    if current.status is not AppointmentStatus.SCHEDULED:
        raise InvalidStateTransition("This appointment is already cancelled.")
    updated = replace(
        current, status=AppointmentStatus.CANCELLED, updated_at=now, version=next_version(current)
    )
    return ChangeSet(
        appointments=[(updated, current.version)],
        audit_events=[
            audit_event(
                actor,
                "appointment.cancelled",
                "appointment",
                current.id,
                now,
                before=current.to_audit_dict(),
                after=updated.to_audit_dict(),
            )
        ],
    )


# --- Proposals -----------------------------------------------------------


def _merge(target: ChangeSet, extra: ChangeSet) -> ChangeSet:
    target.medications += extra.medications
    target.appointments += extra.appointments
    target.proposals += extra.proposals
    target.cancel_future_doses += extra.cancel_future_doses
    target.notifications += extra.notifications
    target.audit_events += extra.audit_events
    return target


def _decided(
    proposal: Proposal, status: ProposalStatus, decider: Member, now: datetime, note: str
) -> Proposal:
    return replace(
        proposal, status=status, decided_by=decider.id, decided_at=now, decision_note=note
    )


def _proposal_effect(
    decider: Member,
    proposal: Proposal,
    target: Medication | Appointment | None,
    now: datetime,
    new_id: Callable[[], str],
) -> ChangeSet:
    """Build the record change an approved proposal makes.

    Uses the same builders as the elder's direct edits, so each change has one definition.
    """
    med, appt = proposal.medication_details, proposal.appointment_details
    kind = proposal.kind
    if kind is ProposalKind.ADD_MEDICATION and med is not None:
        return add_medication(decider, med, now, new_id())
    if (
        kind is ProposalKind.CHANGE_MEDICATION
        and med is not None
        and isinstance(target, Medication)
    ):
        return change_medication(decider, target, med, now)
    if kind is ProposalKind.STOP_MEDICATION and isinstance(target, Medication):
        return stop_medication(decider, target, now)
    if kind is ProposalKind.ADD_APPOINTMENT and appt is not None:
        return add_appointment(decider, appt, now, new_id())
    if (
        kind is ProposalKind.CHANGE_APPOINTMENT
        and appt is not None
        and isinstance(target, Appointment)
    ):
        return change_appointment(decider, target, appt, now)
    if kind is ProposalKind.CANCEL_APPOINTMENT and isinstance(target, Appointment):
        return cancel_appointment(decider, target, now)
    # Only reachable with a malformed proposal (e.g. a hand-edited database row).
    raise InvalidStateTransition("This request is incomplete and cannot be applied.")


def approve_proposal(
    decider: Member,
    proposal: Proposal,
    target: Medication | Appointment | None,
    now: datetime,
    new_id: Callable[[], str],
    note: str = "",
) -> ChangeSet:
    """Describe approving a proposal as one atomic ChangeSet.

    The proposal is marked approved and its change is applied together, or not at all.
    `decider` is the elder or the elder's trusted decision-maker; the caller checks
    permission.

    If the target record changed after the proposal was made, approval is
    refused so the decider never approves something based on stale details.
    """
    if proposal.status is not ProposalStatus.PENDING:
        raise InvalidStateTransition("This request has already been decided.")
    if proposal.base_version is not None and (
        target is None or target.version != proposal.base_version
    ):
        raise ConcurrencyConflict(
            "The record changed after this request was made. "
            "Please reject it and ask for a new one."
        )

    kind = proposal.kind
    effect = _proposal_effect(decider, proposal, target, now, new_id)
    decided = _decided(proposal, ProposalStatus.APPROVED, decider, now, note)
    changes = ChangeSet(
        proposals=[(decided, ProposalStatus.PENDING)],
        audit_events=[
            audit_event(
                decider,
                "proposal.approved",
                "proposal",
                proposal.id,
                now,
                before={"status": proposal.status.value},
                after={
                    "status": decided.status.value,
                    "kind": kind.value,
                    "note": note,
                    "on_behalf_of_elder": decider.role is not Role.ELDER,
                },
            )
        ],
    )
    return _merge(changes, effect)


def reject_proposal(
    decider: Member, proposal: Proposal, now: datetime, note: str = ""
) -> ChangeSet:
    """Describe declining a pending proposal; no record changes."""
    if proposal.status is not ProposalStatus.PENDING:
        raise InvalidStateTransition("This request has already been decided.")
    decided = _decided(proposal, ProposalStatus.REJECTED, decider, now, note)
    return ChangeSet(
        proposals=[(decided, ProposalStatus.PENDING)],
        audit_events=[
            audit_event(
                decider,
                "proposal.rejected",
                "proposal",
                proposal.id,
                now,
                before={"status": proposal.status.value},
                after={
                    "status": decided.status.value,
                    "kind": proposal.kind.value,
                    "note": note,
                    "on_behalf_of_elder": decider.role is not Role.ELDER,
                },
            )
        ],
    )


def withdraw_proposal(proposer: Member, proposal: Proposal, now: datetime) -> ChangeSet:
    """Describe the proposer withdrawing their own pending proposal."""
    if proposal.status is not ProposalStatus.PENDING:
        raise InvalidStateTransition("This request has already been decided.")
    cancelled = replace(proposal, status=ProposalStatus.CANCELLED, decided_at=now)
    return ChangeSet(
        proposals=[(cancelled, ProposalStatus.PENDING)],
        audit_events=[
            audit_event(
                proposer,
                "proposal.cancelled",
                "proposal",
                proposal.id,
                now,
                before={"status": proposal.status.value},
                after={"status": cancelled.status.value},
            )
        ],
    )
