"""HTTP-level security: authentication, CSRF, isolation, escaping, headers."""

from __future__ import annotations

import pytest

from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, medication_details
from tests.web.helpers import CSRF_PATTERN, csrf_from, make_client, post_form, sign_in

PROTECTED_GETS = [
    "/medications",
    "/medications/new",
    "/appointments",
    "/proposals",
    "/history",
    "/activity",
    "/notifications",
    "/circle",
]
PROTECTED_POSTS = [
    "/medications",
    "/appointments",
    "/circle/members",
    "/circle/settings",
    "/doses/x/record",
    "/proposals/x/approve",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_pages_redirect_to_sign_in_without_a_session(services, path):
    response = make_client(services).get(path)
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("path", PROTECTED_POSTS)
def test_actions_need_a_session(services, path):
    client = make_client(services)
    token = csrf_from(client, "/login")
    response = client.post(path, data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_posts_without_a_valid_csrf_token_are_refused(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    form = {"name": "X", "dosage": "1", "time_1": "08:00", "start_date": "2026-11-15"}
    assert client.post("/medications", data=form).status_code == 403
    assert client.post("/medications", data={**form, "csrf_token": "forged"}).status_code == 403
    assert services.records.list_medications(circle.elder) == []


def test_login_form_itself_requires_csrf(services, circle):
    response = make_client(services).post(
        "/login",
        data={"circle_code": circle.code, "display_name": "Asha Rao", "passcode": ELDER_PASSCODE},
    )
    assert response.status_code == 403


def test_records_of_another_circle_are_not_visible(services, circle):
    _, other_elder = services.circles.create_circle(
        elder_name="Other Elder",
        passcode="other-pass-99",
        confirm_passcode="other-pass-99",
        email="",
        timezone="Asia/Kolkata",
    )
    other_med = services.records.add_medication(other_elder, medication_details(name="Secret pill"))
    client = make_client(services)
    sign_in(client, circle.code, "Ravi", CARETAKER_PASSCODE)
    assert client.get(f"/medications/{other_med}").status_code == 404
    assert "Secret pill" not in client.get("/medications").text
    response = post_form(client, f"/medications/{other_med}/stop", {"reason": "x"})
    assert response.status_code == 404


def test_user_input_is_html_escaped(services, circle):
    services.records.add_medication(
        circle.elder, medication_details(name="<script>alert(1)</script>")
    )
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    page = client.get("/medications").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_security_headers_and_cookie_flags(services, circle):
    client = make_client(services)
    response = client.get("/login")
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    login = sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_session_is_replaced_on_sign_in(services, circle):
    client = make_client(services)
    before = CSRF_PATTERN.search(client.get("/login").text).group(1)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    after = CSRF_PATTERN.search(client.get("/").text).group(1)
    assert before != after  # new session, new CSRF token


def test_sign_out_ends_the_session(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    post_form(client, "/logout", {})
    assert client.get("/medications").status_code == 303


def test_open_redirects_are_blocked(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    clock.advance(minutes=31)
    dose = services.doses.today(circle.elder, services.circles.get_circle(circle.elder))[0].dose
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    response = post_form(
        client, f"/doses/{dose.id}/record", {"outcome": "taken", "next": "//evil.example"}
    )
    assert response.headers["location"] == "/"


def test_failed_login_does_not_reveal_which_part_was_wrong(services, circle):
    client = make_client(services)
    texts = []
    for name, passcode in (("Asha Rao", "wrong-pass-1"), ("Nobody Here", ELDER_PASSCODE)):
        token = csrf_from(client, "/login")
        response = client.post(
            "/login",
            data={
                "circle_code": circle.code,
                "display_name": name,
                "passcode": passcode,
                "csrf_token": token,
            },
        )
        assert response.status_code == 401
        assert passcode not in response.text  # passcode never echoed back
        texts.append(response.text.split("<main", 1)[1].split("</main>", 1)[0].replace(name, ""))
    assert texts[0] == texts[1]


def test_api_docs_are_not_exposed(services):
    client = make_client(services)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_malformed_input_gets_a_friendly_error_not_a_stack_trace(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    response = client.get("/history?days=9999")
    assert response.status_code == 400
    assert "Traceback" not in response.text
