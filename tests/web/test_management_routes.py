"""Care-circle management and the remaining proposal routes over HTTP."""

from __future__ import annotations

from careloop.domain.models import AppointmentStatus, MedicationStatus, ProposalStatus
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, FAMILY_PASSCODE, medication_details
from tests.web.helpers import make_client, post_form, sign_in

# Caretakers and family must say who advised a suggestion.
OWN_IDEA = {"advice_source": "care_team"}
DOCTOR_ADVICE = {"advice_source": "doctor", "advised_by": "Dr. Shah", "advised_on": "2026-11-14"}

APPOINTMENT_FORM = {
    "doctor_name": "Dr. Shah",
    "purpose": "Check-up",
    "date": "2026-11-20",
    "time": "10:30",
    "location": "",
    "notes": "",
}


def elder_client(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    return client


def test_elder_adds_a_helper_who_can_then_sign_in(services, circle):
    client = elder_client(services, circle)
    response = post_form(
        client,
        "/circle/members",
        {
            "display_name": "Neha",
            "role": "family",
            "email": "neha@example.com",
            "passcode": "sunny-day-55",
        },
    )
    assert response.status_code == 303
    newcomer = make_client(services)
    sign_in(newcomer, circle.code, "Neha", "sunny-day-55")
    assert newcomer.get("/").status_code == 200


def test_adding_a_helper_validates_input(services, circle):
    client = elder_client(services, circle)
    response = post_form(
        client,
        "/circle/members",
        {"display_name": "Ravi", "role": "elder", "email": "bad", "passcode": "123"},
    )
    assert response.status_code == 422
    assert "caretaker or family" in response.text
    assert "valid email" in response.text


def test_duplicate_member_name_is_refused(services, circle):
    client = elder_client(services, circle)
    response = post_form(
        client,
        "/circle/members",
        {"display_name": "ravi", "role": "family", "email": "", "passcode": "sunny-day-55"},
    )
    assert response.status_code == 422
    assert "already uses that name" in response.text


def test_elder_removes_a_helper_and_their_session_ends(services, circle):
    helper = make_client(services)
    sign_in(helper, circle.code, "Meera", FAMILY_PASSCODE)
    client = elder_client(services, circle)
    post_form(client, f"/circle/members/{circle.family.id}/remove", {})
    assert helper.get("/medications").status_code == 303  # signed out on next request


def test_elder_cannot_be_removed(services, circle):
    client = elder_client(services, circle)
    post_form(client, f"/circle/members/{circle.elder.id}/remove", {})
    assert "cannot be removed" in client.get("/circle").text


def test_helpers_cannot_manage_people_or_settings(services, circle):
    helper = make_client(services)
    sign_in(helper, circle.code, "Ravi", CARETAKER_PASSCODE)
    assert post_form(helper, f"/circle/members/{circle.family.id}/remove", {}).status_code == 403
    assert (
        post_form(
            helper,
            "/circle/settings",
            {
                "caretaker_after_minutes": "5",
                "family_after_minutes": "6",
                "close_after_minutes": "7",
            },
        ).status_code
        == 403
    )
    assert services.ctx.repo.get_member(circle.family.id).active


def test_elder_changes_escalation_timings(services, circle):
    client = elder_client(services, circle)
    bad = post_form(
        client,
        "/circle/settings",
        {
            "caretaker_after_minutes": "60",
            "family_after_minutes": "30",
            "close_after_minutes": "90",
        },
    )
    assert bad.status_code == 422 and "later than the caretaker step" in bad.text
    good = post_form(
        client,
        "/circle/settings",
        {
            "caretaker_after_minutes": "15",
            "family_after_minutes": "45",
            "close_after_minutes": "120",
        },
    )
    assert good.status_code == 303
    assert services.circles.get_circle(circle.elder).policy.caretaker_after_minutes == 15


def test_family_proposes_appointment_and_elder_declines(services, circle):
    family = make_client(services)
    sign_in(family, circle.code, "Meera", FAMILY_PASSCODE)
    response = post_form(family, "/appointments", {**APPOINTMENT_FORM, **DOCTOR_ADVICE})
    assert response.headers["location"] == "/proposals"
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)

    client = elder_client(services, circle)
    response = post_form(client, f"/proposals/{proposal.id}/reject", {"note": ""})
    assert response.status_code == 303
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.REJECTED
    assert services.records.list_appointments(circle.elder) == []


def test_caretaker_withdraws_own_request(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    caretaker = make_client(services)
    sign_in(caretaker, circle.code, "Ravi", CARETAKER_PASSCODE)
    post_form(caretaker, f"/medications/{med_id}/stop", {"reason": "Finished course", **OWN_IDEA})
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)
    assert "Withdraw my request" in caretaker.get(f"/proposals/{proposal.id}").text
    post_form(caretaker, f"/proposals/{proposal.id}/withdraw", {})
    assert services.ctx.repo.get_proposal(proposal.id).status is ProposalStatus.CANCELLED
    assert services.ctx.repo.get_medication(med_id).status is MedicationStatus.ACTIVE


def test_elder_edits_cancels_and_stops_directly(services, circle):
    client = elder_client(services, circle)
    post_form(client, "/appointments", APPOINTMENT_FORM)
    [appointment] = services.records.list_appointments(circle.elder)
    edited = post_form(
        client, f"/appointments/{appointment.id}/edit", {**APPOINTMENT_FORM, "time": "11:15"}
    )
    assert edited.status_code == 303
    assert services.ctx.repo.get_appointment(appointment.id).version == 2
    post_form(client, f"/appointments/{appointment.id}/cancel", {})
    assert services.ctx.repo.get_appointment(appointment.id).status is AppointmentStatus.CANCELLED

    med_id = services.records.add_medication(circle.elder, medication_details())
    post_form(client, f"/medications/{med_id}/stop", {})
    assert services.ctx.repo.get_medication(med_id).status is MedicationStatus.STOPPED


def test_caretaker_suggests_appointment_change_and_cancellation(services, circle):
    client = elder_client(services, circle)
    post_form(client, "/appointments", APPOINTMENT_FORM)
    [appointment] = services.records.list_appointments(circle.elder)
    caretaker = make_client(services)
    sign_in(caretaker, circle.code, "Ravi", CARETAKER_PASSCODE)
    post_form(
        caretaker,
        f"/appointments/{appointment.id}/edit",
        {**APPOINTMENT_FORM, "time": "12:00", **OWN_IDEA},
    )
    post_form(
        caretaker, f"/appointments/{appointment.id}/cancel", {"reason": "Doctor away", **OWN_IDEA}
    )
    kinds = sorted(p.kind.value for p in services.ctx.repo.list_proposals(circle.elder.circle_id))
    assert kinds == ["cancel_appointment", "change_appointment"]
    assert services.ctx.repo.get_appointment(appointment.id).version == 1  # untouched


def test_invalid_appointment_shows_errors(services, circle):
    client = elder_client(services, circle)
    response = post_form(client, "/appointments", {**APPOINTMENT_FORM, "date": "2020-01-01"})
    assert response.status_code == 422 and "must be in the future" in response.text


def test_unknown_record_ids_return_not_found(services, circle):
    client = elder_client(services, circle)
    for path in (
        "/medications/nope",
        "/medications/nope/edit",
        "/appointments/nope/edit",
        "/proposals/nope",
    ):
        assert client.get(path).status_code == 404
