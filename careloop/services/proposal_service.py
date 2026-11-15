"""Consent-based change management.

Caretakers and family propose; the elder decides - or the one helper the elder
has trusted to decide on their behalf, and never on their own suggestion. A
proposal is stored on its own and the active record is untouched until
approval, at which point the proposal status and the record change are written
in one transaction.
"""

from __future__ import annotations

from careloop.domain import changes
from careloop.domain.errors import NotFound, PermissionDenied, ValidationFailed
from careloop.domain.models import (
    APPOINTMENT_PROPOSAL_KINDS,
    Advice,
    AdviceSource,
    Appointment,
    AppointmentDetails,
    AppointmentStatus,
    ChangeSet,
    Circle,
    Medication,
    MedicationDetails,
    MedicationStatus,
    Member,
    Proposal,
    ProposalKind,
    ProposalStatus,
    Role,
)
from careloop.domain.permissions import Action, authorize, authorize_decision
from careloop.domain.proposal_summary import summarise_proposal
from careloop.domain.validation import validate_reason
from careloop.notifications.messages import proposal_notifications
from careloop.services.context import ServiceContext
from careloop.services.dose_service import DoseService

NEEDS_TARGET = {
    ProposalKind.CHANGE_MEDICATION,
    ProposalKind.STOP_MEDICATION,
    ProposalKind.CHANGE_APPOINTMENT,
    ProposalKind.CANCEL_APPOINTMENT,
}
NEEDS_MEDICATION_DETAILS = {ProposalKind.ADD_MEDICATION, ProposalKind.CHANGE_MEDICATION}
NEEDS_APPOINTMENT_DETAILS = {ProposalKind.ADD_APPOINTMENT, ProposalKind.CHANGE_APPOINTMENT}


class ProposalService:
    """Suggesting, deciding and withdrawing changes."""

    def __init__(self, ctx: ServiceContext, doses: DoseService) -> None:
        self._ctx = ctx
        self._doses = doses

    # --- Helpers -------------------------------------------------------------

    def _circle(self, member: Member) -> Circle:
        circle = self._ctx.repo.get_circle(member.circle_id)
        if circle is None:
            raise NotFound()
        return circle

    def _target(
        self, kind: ProposalKind, target_id: str | None, circle_id: str
    ) -> Medication | Appointment | None:
        if kind not in NEEDS_TARGET:
            return None
        if not target_id:
            raise NotFound()
        repo = self._ctx.repo
        target: Medication | Appointment | None = (
            repo.get_appointment(target_id)
            if kind in APPOINTMENT_PROPOSAL_KINDS
            else repo.get_medication(target_id)
        )
        if target is None or target.circle_id != circle_id:
            raise NotFound()
        return target

    @staticmethod
    def _check_target_is_open(target: Medication | Appointment | None) -> None:
        if isinstance(target, Medication) and target.status is not MedicationStatus.ACTIVE:
            raise ValidationFailed({"target": "That medicine has already been stopped."})
        if isinstance(target, Appointment) and target.status is not AppointmentStatus.SCHEDULED:
            raise ValidationFailed({"target": "That appointment has already been cancelled."})

    # --- Proposing -------------------------------------------------------------

    def propose(
        self,
        proposer: Member,
        *,
        kind: ProposalKind,
        target_id: str | None = None,
        medication_details: MedicationDetails | None = None,
        appointment_details: AppointmentDetails | None = None,
        reason: str = "",
        advice: Advice | None = None,
    ) -> Proposal:
        """Store a suggestion and notify whoever may decide on it; changes no record."""
        authorize(proposer, Action.CREATE_PROPOSAL, proposer.circle_id)
        if kind in NEEDS_MEDICATION_DETAILS and medication_details is None:
            raise ValidationFailed({"name": "Please fill in the medicine details."})
        if kind in NEEDS_APPOINTMENT_DETAILS and appointment_details is None:
            raise ValidationFailed({"doctor_name": "Please fill in the appointment details."})
        target = self._target(kind, target_id, proposer.circle_id)
        self._check_target_is_open(target)

        now = self._ctx.clock.now()
        proposal = Proposal(
            id=self._ctx.id_factory(),
            circle_id=proposer.circle_id,
            kind=kind,
            target_id=target.id if target is not None else None,
            base_version=target.version if target is not None else None,
            medication_details=medication_details if kind in NEEDS_MEDICATION_DETAILS else None,
            appointment_details=appointment_details if kind in NEEDS_APPOINTMENT_DETAILS else None,
            reason=validate_reason(reason),
            status=ProposalStatus.PENDING,
            proposed_by=proposer.id,
            proposed_by_role=proposer.role,
            created_at=now,
            advice=advice or Advice(AdviceSource.CARE_TEAM),
        )
        circle = self._circle(proposer)
        deciders = [
            m
            for m in self._ctx.repo.list_members(circle.id)
            if m.active
            and m.id != proposer.id
            and (m.role is Role.ELDER or m.id == circle.decision_maker_id)
        ]
        summary = summarise_proposal(proposal, target, proposer.display_name, circle.timezone)
        self._ctx.repo.apply_changeset(
            ChangeSet(
                proposals=[(proposal, None)],
                notifications=proposal_notifications(
                    circle,
                    proposal,
                    proposer,
                    deciders,
                    summary,
                    self._ctx.settings.public_url,
                    now,
                ),
                audit_events=[
                    changes.audit_event(
                        proposer,
                        "proposal.created",
                        "proposal",
                        proposal.id,
                        now,
                        after={
                            "kind": kind.value,
                            "target_id": proposal.target_id,
                            "proposed": proposal.payload_dict(),
                            "reason": proposal.reason,
                            "advice_source": proposal.advice.source.value,
                            "advised_by": proposal.advice.advised_by,
                        },
                    )
                ],
            )
        )
        return proposal

    # --- Reading ---------------------------------------------------------------

    def get(self, viewer: Member, proposal_id: str) -> Proposal:
        """Return one proposal from the viewer's circle."""
        proposal = self._ctx.repo.get_proposal(proposal_id)
        if proposal is None or proposal.circle_id != viewer.circle_id:
            raise NotFound()
        authorize(viewer, Action.VIEW_CIRCLE, proposal.circle_id)
        return proposal

    def list(self, viewer: Member, status: ProposalStatus | None = None) -> list[Proposal]:
        """Return the circle's proposals, newest first, optionally by status."""
        authorize(viewer, Action.VIEW_CIRCLE, viewer.circle_id)
        return self._ctx.repo.list_proposals(viewer.circle_id, status)

    def current_target(self, viewer: Member, proposal: Proposal) -> Medication | Appointment | None:
        """The record a change/stop/cancel proposal points at, for side-by-side review."""
        if proposal.target_id is None:
            return None
        return self._target(proposal.kind, proposal.target_id, viewer.circle_id)

    def summary(self, viewer: Member, proposal: Proposal) -> str:
        """Plain-language description of a proposal, including who advised it."""
        circle = self._circle(viewer)
        proposer = self._ctx.repo.get_member(proposal.proposed_by)
        name = proposer.display_name if proposer else "Someone"
        return summarise_proposal(
            proposal, self.current_target(viewer, proposal), name, circle.timezone
        )

    # --- Deciding --------------------------------------------------------------

    def approve(self, decider: Member, proposal_id: str, note: str = "") -> None:
        """Approve a proposal and apply its change in one transaction."""
        proposal = self.get(decider, proposal_id)
        circle = self._circle(decider)
        authorize_decision(decider, circle, proposal)
        target = self.current_target(decider, proposal)
        now = self._ctx.clock.now()
        change_set = changes.approve_proposal(
            decider, proposal, target, now, self._ctx.id_factory, validate_reason(note)
        )
        self._ctx.repo.apply_changeset(change_set)
        for medication, _ in change_set.medications:
            saved = self._ctx.repo.get_medication(medication.id)
            if saved is not None:
                self._doses.generate_for_medication(circle, saved, now)

    def reject(self, decider: Member, proposal_id: str, note: str = "") -> None:
        """Decline a proposal; nothing else changes."""
        proposal = self.get(decider, proposal_id)
        authorize_decision(decider, self._circle(decider), proposal)
        self._ctx.repo.apply_changeset(
            changes.reject_proposal(decider, proposal, self._ctx.clock.now(), validate_reason(note))
        )

    def withdraw(self, proposer: Member, proposal_id: str) -> None:
        """Let the proposer withdraw their own pending proposal."""
        proposal = self.get(proposer, proposal_id)
        if proposal.proposed_by != proposer.id:
            raise PermissionDenied("Only the person who made this request can withdraw it.")
        self._ctx.repo.apply_changeset(
            changes.withdraw_proposal(proposer, proposal, self._ctx.clock.now())
        )
