"""Plain-language request summaries, advice validation and decision rights."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from careloop.domain.errors import PermissionDenied, ValidationFailed
from careloop.domain.models import (
    Advice,
    AdviceSource,
    Appointment,
    AppointmentDetails,
    AppointmentStatus,
    Circle,
    EscalationPolicy,
    Medication,
    MedicationDetails,
    MedicationStatus,
    Member,
    Proposal,
    ProposalKind,
    ProposalStatus,
    Role,
)
from careloop.domain.permissions import authorize_decision, can_decide_proposals
from careloop.domain.proposal_summary import (
    clock_time,
    describe_advice,
    join_words,
    summarise_proposal,
)
from careloop.domain.validation import validate_advice

NOW = datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)
TZ = "Asia/Kolkata"
MED = Medication(
    "m1",
    "c1",
    MedicationDetails("Metformin", "1 tablet", "With food", ("08:00",), date(2026, 11, 1), None),
    MedicationStatus.ACTIVE,
    1,
    NOW,
    NOW,
)
APPT = Appointment(
    "a1",
    "c1",
    AppointmentDetails(
        "Dr. Shah", "Check-up", "City Clinic", datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc), ""
    ),
    AppointmentStatus.SCHEDULED,
    1,
    NOW,
    NOW,
)


def proposal(kind: ProposalKind, *, med=None, appt=None, advice=None) -> Proposal:
    return Proposal(
        "p1",
        "c1",
        kind,
        None,
        None,
        med,
        appt,
        "",
        ProposalStatus.PENDING,
        "u1",
        Role.CARETAKER,
        NOW,
        advice=advice or Advice(AdviceSource.CARE_TEAM),
    )


def test_helpers_read_naturally():
    assert clock_time("08:00") == "8:00 AM" and clock_time("20:30") == "8:30 PM"
    assert join_words(["a"]) == "a"
    assert join_words(["a", "b", "c"]) == "a, b and c"


def test_change_lists_only_what_differs_in_everyday_words():
    new = MedicationDetails(
        "Metformin", "2 tablets", "With food", ("09:00",), date(2026, 11, 1), None
    )
    advice = Advice(AdviceSource.DOCTOR, "Dr. Shah", date(2026, 11, 12))
    text = summarise_proposal(
        proposal(ProposalKind.CHANGE_MEDICATION, med=new, advice=advice), MED, "Ravi", TZ
    )
    assert text == (
        'Ravi suggests changing Metformin: the dose from "1 tablet" to "2 tablets" and '
        "the time from 8:00 AM to 9:00 AM. Advised by Dr. Shah on 12 Nov 2026."
    )


def test_own_suggestion_is_labelled_as_not_medical_advice():
    text = summarise_proposal(proposal(ProposalKind.STOP_MEDICATION), MED, "Meera", TZ)
    assert text == (
        "Meera suggests stopping Metformin (1 tablet at 8:00 AM). "
        "This is Meera's own suggestion, not a doctor's instruction."
    )


def test_add_and_cancel_appointment_use_local_time():
    added = summarise_proposal(
        proposal(ProposalKind.ADD_APPOINTMENT, appt=APPT.details), None, "Ravi", TZ
    )
    assert "on 20 Nov 2026 at 10:30 AM for Check-up" in added
    cancelled = summarise_proposal(proposal(ProposalKind.CANCEL_APPOINTMENT), APPT, "Ravi", TZ)
    assert cancelled.startswith("Ravi suggests cancelling the appointment with Dr. Shah")


def test_add_medication_mentions_dose_times_and_dates():
    new = MedicationDetails(
        "Vitamin D", "1 capsule", "", ("08:00", "20:00"), date(2026, 11, 16), date(2026, 12, 16)
    )
    text = summarise_proposal(proposal(ProposalKind.ADD_MEDICATION, med=new), None, "Ravi", TZ)
    assert (
        "adding Vitamin D: 1 capsule at 8:00 AM and 8:00 PM, starting 16 Nov 2026 until 16 Dec 2026"
        in text
    )


def test_pharmacist_and_unnamed_doctor_wording():
    assert (
        describe_advice(Advice(AdviceSource.PHARMACIST, "Anil"), "Ravi")
        == "Advised by pharmacist Anil."
    )
    assert (
        describe_advice(Advice(AdviceSource.DOCTOR, "Mehta"), "Ravi") == "Advised by doctor Mehta."
    )


def test_advice_validation():
    today = date(2026, 11, 15)
    assert validate_advice("care_team", "", "", today=today).source is AdviceSource.CARE_TEAM
    with pytest.raises(ValidationFailed) as err:
        validate_advice("doctor", "", "2026-11-20", today=today)
    assert set(err.value.field_errors) == {"advised_by", "advised_on"}
    with pytest.raises(ValidationFailed):
        validate_advice("my neighbour", "", "", today=today)


def _member(member_id: str, role: Role, active: bool = True) -> Member:
    return Member(member_id, "c1", member_id, role, None, "h", 0, None, active, NOW)


def _circle(decision_maker_id: str | None) -> Circle:
    return Circle("c1", "ABCD2345", "Asha", TZ, EscalationPolicy(), NOW, decision_maker_id)


def test_only_elder_and_trusted_helper_may_decide():
    elder, ravi, meera = (
        _member("e", Role.ELDER),
        _member("r", Role.CARETAKER),
        _member("m", Role.FAMILY),
    )
    assert can_decide_proposals(elder, _circle(None))
    assert not can_decide_proposals(ravi, _circle(None))
    assert can_decide_proposals(ravi, _circle("r"))
    assert not can_decide_proposals(meera, _circle("r"))
    assert not can_decide_proposals(_member("r", Role.CARETAKER, active=False), _circle("r"))


def test_nobody_decides_on_their_own_suggestion():
    ravi = _member("r", Role.CARETAKER)
    own = Proposal(
        "p1",
        "c1",
        ProposalKind.STOP_MEDICATION,
        "m1",
        1,
        None,
        None,
        "",
        ProposalStatus.PENDING,
        "r",
        Role.CARETAKER,
        NOW,
    )
    with pytest.raises(PermissionDenied):
        authorize_decision(ravi, _circle("r"), own)
