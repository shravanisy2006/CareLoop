"""Full user journeys through the HTTP interface."""

from __future__ import annotations

from careloop.domain.models import DoseStatus, ProposalStatus
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, FAMILY_PASSCODE, medication_details
from tests.web.helpers import CSRF_PATTERN, make_client, post_form, sign_in

MEDICINE_FORM = {
    "name": "Amlodipine",
    "dosage": "1 tablet (5 mg)",
    "instructions": "In the morning",
    "time_1": "09:00",
    "time_2": "",
    "time_3": "",
    "time_4": "",
    "start_date": "2026-11-15",
    "end_date": "",
}


def test_start_a_circle_then_see_the_code(services):
    client = make_client(services)
    token = CSRF_PATTERN.search(client.get("/start").text).group(1)
    response = client.post(
        "/start",
        data={
            "elder_name": "Kamala Iyer",
            "email": "kamala@example.com",
            "passcode": "lotus-pond-88",
            "confirm_passcode": "lotus-pond-88",
            "timezone": "Asia/Kolkata",
            "csrf_token": token,
        },
    )
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    welcome = client.get("/welcome")
    assert "Your care circle code" in welcome.text
    assert client.get("/").status_code == 200


def test_start_form_shows_field_errors(services):
    client = make_client(services)
    token = CSRF_PATTERN.search(client.get("/start").text).group(1)
    response = client.post(
        "/start",
        data={
            "elder_name": "",
            "email": "not-an-email",
            "passcode": "123456",
            "confirm_passcode": "different",
            "timezone": "Asia/Kolkata",
            "csrf_token": token,
        },
    )
    assert response.status_code == 422
    for text in ("Please enter a name", "valid email", "too easy to guess", "do not match"):
        assert text in response.text
    assert 'aria-invalid="true"' in response.text


def test_elder_marks_dose_taken_from_home_page(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    clock.advance(minutes=31)  # 08:01 local, dose is due
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    home = client.get("/")
    assert "I took it" in home.text
    dose = services.doses.today(circle.elder, services.circles.get_circle(circle.elder))[0].dose
    response = post_form(client, f"/doses/{dose.id}/record", {"outcome": "taken"})
    assert response.status_code == 303
    assert "Recorded as taken" in client.get("/").text
    assert services.ctx.repo.get_dose(dose.id).status is DoseStatus.TAKEN


def test_caretaker_change_waits_for_elder_approval(services, circle):
    med_id = services.records.add_medication(circle.elder, medication_details())
    caretaker = make_client(services)
    sign_in(caretaker, circle.code, "Ravi", CARETAKER_PASSCODE)
    changed = {**MEDICINE_FORM, "name": "Metformin", "time_1": "09:00", "reason": "Doctor said 9am"}
    response = post_form(caretaker, f"/medications/{med_id}/edit", changed)
    assert response.status_code == 303 and response.headers["location"] == "/proposals"

    # Active record is untouched until the elder decides.
    assert services.ctx.repo.get_medication(med_id).details.times == ("08:00",)
    [proposal] = services.ctx.repo.list_proposals(circle.elder.circle_id)
    assert proposal.status is ProposalStatus.PENDING

    # The caretaker cannot approve their own proposal.
    denied = post_form(caretaker, f"/proposals/{proposal.id}/approve", {"note": ""})
    assert denied.status_code == 403

    elder = make_client(services)
    sign_in(elder, circle.code, "Asha Rao", ELDER_PASSCODE)
    assert "change needs your decision" in elder.get("/").text
    detail = elder.get(f"/proposals/{proposal.id}")
    assert "Yes, approve" in detail.text and "changed" in detail.text
    approved = post_form(
        elder,
        f"/proposals/{proposal.id}/approve",
        {"note": "OK"},
        token_page=f"/proposals/{proposal.id}",
    )
    assert approved.status_code == 303
    assert services.ctx.repo.get_medication(med_id).details.times == ("09:00",)


def test_family_member_cannot_record_doses(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    clock.advance(minutes=31)
    dose = services.doses.today(circle.elder, services.circles.get_circle(circle.elder))[0].dose
    family = make_client(services)
    sign_in(family, circle.code, "Meera", FAMILY_PASSCODE)
    assert "Record as taken" not in family.get("/").text
    response = post_form(family, f"/doses/{dose.id}/record", {"outcome": "taken"})
    assert response.status_code == 403
    assert services.ctx.repo.get_dose(dose.id).status is DoseStatus.SCHEDULED


def test_every_main_page_renders_for_every_role(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    clock.advance(minutes=45)
    services.cycle.run_once()
    pages = [
        "/",
        "/medications",
        "/medications/new",
        "/appointments",
        "/appointments/new",
        "/proposals",
        "/history",
        "/history?days=30",
        "/activity",
        "/notifications",
        "/circle",
    ]
    for name, passcode in (
        ("Asha Rao", ELDER_PASSCODE),
        ("Ravi", CARETAKER_PASSCODE),
        ("Meera", FAMILY_PASSCODE),
    ):
        client = make_client(services)
        sign_in(client, circle.code, name, passcode)
        for page in pages:
            response = client.get(page)
            assert response.status_code == 200, (name, page, response.status_code)


def test_elder_adds_appointment_with_calendar_link(services, circle):
    elder = make_client(services)
    sign_in(elder, circle.code, "Asha Rao", ELDER_PASSCODE)
    response = post_form(
        elder,
        "/appointments",
        {
            "doctor_name": "Dr. Shah",
            "purpose": "Diabetes check-up",
            "date": "2026-11-20",
            "time": "10:30",
            "location": "City Clinic",
            "notes": "",
        },
    )
    assert response.status_code == 303
    page = elder.get("/appointments")
    assert "Dr. Shah" in page.text
    assert "calendar.google.com/calendar/render?action=TEMPLATE" in page.text
