"""Helpers for driving the web app like a person with a browser would."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from careloop.web.app import create_app

CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


def make_client(services) -> TestClient:
    app = create_app(settings=services.ctx.settings, services=services)
    return TestClient(app, follow_redirects=False)


def csrf_from(client: TestClient, path: str = "/login") -> str:
    page = client.get(path)
    match = CSRF_PATTERN.search(page.text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def post_form(
    client: TestClient,
    path: str,
    data: dict[str, str],
    token_page: str = "/",  # noqa: S107 - a page path, not a password
):
    token = csrf_from(client, token_page)
    return client.post(path, data={**data, "csrf_token": token})


def sign_in(client: TestClient, code: str, name: str, passcode: str):
    token = csrf_from(client, "/login")
    response = client.post(
        "/login",
        data={"circle_code": code, "display_name": name, "passcode": passcode, "csrf_token": token},
    )
    assert response.status_code == 303, response.text
    return response
