"""Trusted decision-maker, "who advised this" and request summaries over HTTP."""

from __future__ import annotations

from careloop.domain.models import AppointmentStatus, MedicationStatus, ProposalStatus
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, FAMILY_PASSCODE, medication_details
from tests.web.helpers import make_client, post_form, sign_in

DOCTOR = {"advice_source": "doctor", "advised_by": "Dr. Shah", "advised_on": "2026-11-14"}


def signed_in(services, circle, name, passcode):
    client = make_client(services)
    sign_in(client, circle.code, name, passcode)
    return client


def test_suggestion_from_doctor_needs_the_doctors_name(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    family = signed_in(services, circle, "Meera", FAMILY_PASSCODE)
    response = post_form(family, f"/medications/{med_id}/stop", {"advice_source": "doctor"})
    assert response.status_code == 422
    assert "Please enter the doctor&#39;s name." in response.text
    assert services.ctx.repo.list_proposals(circle.elder.circle_id) == []


def test_request_page_shows_plain_summary_and_who_advised(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    family = signed_in(services, circle, "Meera", FAMILY_PASSCODE)
    post_form(family, f"/medications/{med_id}/stop", {"reason": "Course finished", **DOCTOR})
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)

    elder = signed_in(services, circle, "Asha Rao", ELDER_PASSCODE)
    page = elder.get(f"/proposals/{proposal.id}").text
    assert "In short" in page
    assert "Meera suggests stopping Metformin (1 tablet (500 mg) at 8:00 AM)." in page
    assert "Advised by Dr. Shah on 14 Nov 2026." in page
    assert "Meera suggests stopping Metformin" in elder.get("/proposals").text


def test_elder_names_a_decision_maker_who_then_decides(services, circle):
    elder = signed_in(services, circle, "Asha Rao", ELDER_PASSCODE)
    response = post_form(elder, "/circle/decision-maker", {"member_id": circle.caretaker.id})
    assert response.status_code == 303
    assert "can approve or decline suggested changes on Asha Rao" in elder.get("/circle").text

    med_id = services.records.add_medication(circle.elder, medication_details())
    family = signed_in(services, circle, "Meera", FAMILY_PASSCODE)
    post_form(family, f"/medications/{med_id}/stop", DOCTOR)
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)

    caretaker = signed_in(services, circle, "Ravi", CARETAKER_PASSCODE)
    assert "1 change needs a decision" in caretaker.get("/").text
    detail = caretaker.get(f"/proposals/{proposal.id}").text
    assert "trusted you to decide on their behalf" in detail
    post_form(
        caretaker,
        f"/proposals/{proposal.id}/approve",
        {"note": ""},
        token_page=f"/proposals/{proposal.id}",
    )
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.APPROVED
    assert services.ctx.repo.get_medication(med_id).status is MedicationStatus.STOPPED


def test_decision_maker_cannot_decide_own_request_in_the_browser(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    med_id = services.records.add_medication(circle.elder, medication_details())
    caretaker = signed_in(services, circle, "Ravi", CARETAKER_PASSCODE)
    post_form(caretaker, f"/medications/{med_id}/stop", DOCTOR)
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)
    page = caretaker.get(f"/proposals/{proposal.id}").text
    assert "someone else must decide" in page and "Yes, approve" not in page
    assert post_form(caretaker, f"/proposals/{proposal.id}/approve", {}).status_code == 403


def test_helpers_cannot_choose_a_decision_maker(services, circle):
    caretaker = signed_in(services, circle, "Ravi", CARETAKER_PASSCODE)
    response = post_form(caretaker, "/circle/decision-maker", {"member_id": circle.caretaker.id})
    assert response.status_code == 403
    assert services.circles.get_circle(circle.elder).decision_maker_id is None


def test_cancelling_an_appointment_asks_for_confirmation(services, circle):
    elder = signed_in(services, circle, "Asha Rao", ELDER_PASSCODE)
    post_form(
        elder,
        "/appointments",
        {"doctor_name": "Dr. Shah", "purpose": "Check-up", "date": "2026-11-20", "time": "10:30"},
    )
    [appointment] = services.records.list_appointments(circle.elder)
    page = elder.get(f"/appointments/{appointment.id}/cancel").text
    assert "Cancel this appointment?" in page and "Yes, cancel it" in page
    assert services.ctx.repo.get_appointment(appointment.id).status is AppointmentStatus.SCHEDULED
    post_form(elder, f"/appointments/{appointment.id}/cancel", {})
    assert services.ctx.repo.get_appointment(appointment.id).status is AppointmentStatus.CANCELLED


def test_doctor_visit_summary_reports_real_doses_and_approved_changes(services, circle, clock):
    med_id = services.records.add_medication(circle.elder, medication_details())
    dose = services.doses.today(circle.elder, services.circles.get_circle(circle.elder))[0].dose
    clock.set(dose.scheduled_for)
    services.doses.record(circle.elder, dose.id, "taken")
    family = signed_in(services, circle, "Meera", FAMILY_PASSCODE)
    post_form(family, f"/medications/{med_id}/stop", DOCTOR)
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)
    services.proposals.approve(circle.elder, proposal.id)

    page = signed_in(services, circle, "Ravi", CARETAKER_PASSCODE).get("/report?days=30").text
    assert "Doctor visit summary: Asha Rao" in page
    assert "Metformin (stopped)" in page and "1 (1 on time)" in page
    assert "advised by a doctor (Dr. Shah)" in page
