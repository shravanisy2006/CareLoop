"""Consent-based changes: nothing changes until the elder approves."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careloop.domain.errors import (
    ConcurrencyConflict,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from careloop.domain.models import (
    AppointmentStatus,
    DoseStatus,
    MedicationStatus,
    NotificationKind,
    ProposalKind,
    ProposalStatus,
)
from careloop.domain.validation import validate_appointment_input
from tests.conftest import medication_details


def appointment_details(services, **overrides):
    values = dict(
        doctor_name="Dr. Shah",
        purpose="Check-up",
        location="City Clinic",
        date_value="2026-11-20",
        time_value="10:30",
        notes="",
        tz_name="Asia/Kolkata",
        now=services.ctx.clock.now(),
    )
    values.update(overrides)
    return validate_appointment_input(**values)


def test_proposal_does_not_touch_the_active_record(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med_id,
        medication_details=medication_details(dosage="2 tablets"),
        reason="Doctor's advice",
    )
    assert services.ctx.repo.get_medication(med_id).details.dosage == "1 tablet (500 mg)"


def test_elder_is_emailed_about_a_pending_proposal(services, circle, sender):
    proposal = services.proposals.propose(
        circle.family, kind=ProposalKind.ADD_MEDICATION, medication_details=medication_details()
    )
    services.cycle.run_once()
    [email] = sender.to("asha@example.com")
    assert "Nothing changes unless you approve it" in email.body
    kinds = {n.kind for n in services.history.notifications(circle.elder)}
    assert NotificationKind.PROPOSAL_PENDING in kinds
    assert proposal.status is ProposalStatus.PENDING


def test_approval_applies_change_and_records_history(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    proposal = services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med_id,
        medication_details=medication_details(times="09:00"),
    )
    services.proposals.approve(circle.elder, proposal.id, "Fine")

    medication = services.ctx.repo.get_medication(med_id)
    assert medication.details.times == ("09:00",) and medication.version == 2
    decided = services.ctx.repo.get_proposal(proposal.id)
    assert decided.status is ProposalStatus.APPROVED and decided.decided_by == circle.elder.id

    actions = [e.action for e in services.history.audit_events(circle.elder)]
    assert "proposal.approved" in actions and "medication.changed" in actions
    change = next(
        e for e in services.history.audit_events(circle.elder) if e.action == "medication.changed"
    )
    assert change.before["times"] == ["08:00"] and change.after["times"] == ["09:00"]


def test_approved_schedule_change_replaces_future_doses(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    proposal = services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med_id,
        medication_details=medication_details(times="09:00"),
    )
    services.proposals.approve(circle.elder, proposal.id)
    now = services.ctx.clock.now()
    doses = services.ctx.repo.list_doses(circle.elder.circle_id, now, now.replace(day=now.day + 2))
    live = [d for d in doses if d.status is DoseStatus.SCHEDULED]
    assert live and all(
        d.scheduled_for.astimezone(timezone.utc).minute == 30 and d.scheduled_for.hour == 3
        for d in live
    )  # 09:00 IST = 03:30 UTC
    assert any(d.status is DoseStatus.CANCELLED for d in doses)


def test_rejection_changes_nothing(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    proposal = services.proposals.propose(
        circle.family, kind=ProposalKind.STOP_MEDICATION, target_id=med_id
    )
    services.proposals.reject(circle.elder, proposal.id, "I still need it")
    assert services.ctx.repo.get_medication(med_id).status is MedicationStatus.ACTIVE
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.REJECTED


@pytest.mark.parametrize("who", ["caretaker", "family"])
def test_only_the_elder_can_decide(services, circle, who):
    proposal = services.proposals.propose(
        circle.caretaker, kind=ProposalKind.ADD_MEDICATION, medication_details=medication_details()
    )
    with pytest.raises(PermissionDenied):
        services.proposals.approve(getattr(circle, who), proposal.id)
    with pytest.raises(PermissionDenied):
        services.proposals.reject(getattr(circle, who), proposal.id)


def test_caregivers_cannot_edit_records_directly(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    for helper in (circle.caretaker, circle.family):
        with pytest.raises(PermissionDenied):
            services.records.change_medication(
                helper, med_id, medication_details(dosage="3 tablets")
            )
        with pytest.raises(PermissionDenied):
            services.records.add_medication(helper, medication_details())


def test_a_proposal_cannot_be_decided_twice(services, circle):
    proposal = services.proposals.propose(
        circle.caretaker, kind=ProposalKind.ADD_MEDICATION, medication_details=medication_details()
    )
    services.proposals.approve(circle.elder, proposal.id)
    with pytest.raises(InvalidStateTransition):
        services.proposals.approve(circle.elder, proposal.id)
    assert len(services.records.list_medications(circle.elder)) == 1


def test_stale_proposal_is_refused_after_record_changed(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    proposal = services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med_id,
        medication_details=medication_details(dosage="2 tablets"),
    )
    services.records.change_medication(
        circle.elder, med_id, medication_details(instructions="After food")
    )
    with pytest.raises(ConcurrencyConflict):
        services.proposals.approve(circle.elder, proposal.id)
    assert services.ctx.repo.get_medication(med_id).details.dosage == "1 tablet (500 mg)"
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.PENDING


def test_only_the_proposer_can_withdraw(services, circle):
    proposal = services.proposals.propose(
        circle.caretaker, kind=ProposalKind.ADD_MEDICATION, medication_details=medication_details()
    )
    with pytest.raises(PermissionDenied):
        services.proposals.withdraw(circle.family, proposal.id)
    services.proposals.withdraw(circle.caretaker, proposal.id)
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.CANCELLED


def test_appointment_proposals_follow_the_same_rule(services, circle):
    proposal = services.proposals.propose(
        circle.family,
        kind=ProposalKind.ADD_APPOINTMENT,
        appointment_details=appointment_details(services),
    )
    assert services.records.list_appointments(circle.elder) == []
    services.proposals.approve(circle.elder, proposal.id)
    [appointment] = services.records.list_appointments(circle.elder)
    assert appointment.details.starts_at == datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)

    change = services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_APPOINTMENT,
        target_id=appointment.id,
        appointment_details=appointment_details(services, time_value="11:00"),
    )
    services.proposals.approve(circle.elder, change.id)
    assert services.ctx.repo.get_appointment(appointment.id).details.starts_at.hour == 5
    assert services.ctx.repo.get_appointment(appointment.id).details.starts_at.minute == 30

    cancel = services.proposals.propose(
        circle.caretaker, kind=ProposalKind.CANCEL_APPOINTMENT, target_id=appointment.id
    )
    services.proposals.approve(circle.elder, cancel.id)
    assert services.ctx.repo.get_appointment(appointment.id).status is AppointmentStatus.CANCELLED


def test_proposals_cannot_target_another_circles_records(services, circle):
    _, other_elder = services.circles.create_circle(
        elder_name="Other Elder",
        passcode="other-pass-99",
        confirm_passcode="other-pass-99",
        email="",
        timezone="Asia/Kolkata",
    )
    other_med = services.records.add_medication(other_elder, medication_details())
    with pytest.raises(NotFound):
        services.proposals.propose(
            circle.caretaker, kind=ProposalKind.STOP_MEDICATION, target_id=other_med
        )


def test_proposal_without_details_is_rejected(services, circle):
    with pytest.raises(ValidationFailed):
        services.proposals.propose(circle.caretaker, kind=ProposalKind.ADD_MEDICATION)
