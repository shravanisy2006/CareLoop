"""The text-size control: works before sign-in, is remembered, needs CSRF."""

from __future__ import annotations

from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE
from tests.web.helpers import csrf_from, make_client, sign_in


def choose(client, size: str, page: str = "/login"):
    token = csrf_from(client, page)
    return client.post("/text-size", data={"size": size, "next": page, "csrf_token": token})


def test_text_size_control_is_on_the_sign_in_page(services):
    page = make_client(services).get("/login").text
    assert 'class="text-normal"' in page
    for label in ("Normal", "Large", "Extra large"):
        assert label in page
    assert 'aria-pressed="true"' in page


def test_choosing_a_size_applies_it_and_returns_to_the_page(services):
    client = make_client(services)
    response = choose(client, "xlarge")
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert 'class="text-xlarge"' in client.get("/login").text


def test_choice_is_kept_after_signing_in(services, circle):
    client = make_client(services)
    choose(client, "large")
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    assert 'class="text-large"' in client.get("/").text


def test_unknown_size_is_ignored(services):
    client = make_client(services)
    choose(client, "huge; color:red")
    assert 'class="text-normal"' in client.get("/login").text


def test_text_size_change_requires_csrf(services):
    client = make_client(services)
    client.get("/login")
    assert client.post("/text-size", data={"size": "large"}).status_code == 403


def test_elder_pages_are_larger_by_default(services, circle):
    elder = make_client(services)
    sign_in(elder, circle.code, "Asha Rao", ELDER_PASSCODE)
    assert 'class="elder-view"' in elder.get("/").text
    helper = make_client(services)
    sign_in(helper, circle.code, "Ravi", CARETAKER_PASSCODE)
    assert 'class="elder-view"' not in helper.get("/").text
