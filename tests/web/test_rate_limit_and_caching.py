"""Per-address sign-in rate limit, static asset caching, compression and HSTS."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from careloop.services.container import build_services
from careloop.storage.sqlite_repository import SqliteRepository
from careloop.web.app import create_app
from careloop.web.rate_limit import MAX_TRACKED_CLIENTS, SlidingWindowLimiter, TooManyAttempts
from careloop.web.security import STATIC_CACHE_VERSIONED
from tests.conftest import ELDER_PASSCODE, make_settings
from tests.web.helpers import csrf_from, make_client


def _limited_client(tmp_path, clock, sender, attempts: int) -> TestClient:
    settings = make_settings(tmp_path, sign_in_attempts_per_window=attempts)
    services = build_services(
        settings, repo=SqliteRepository(settings.sqlite_path), clock=clock, email_sender=sender
    )
    return TestClient(create_app(settings, services), follow_redirects=False)


def _try_sign_in(client: TestClient, code: str, name: str, passcode: str) -> int:
    token = csrf_from(client, "/login")
    data = {"circle_code": code, "display_name": name, "passcode": passcode, "csrf_token": token}
    return client.post("/login", data=data).status_code


# --- limiter unit behaviour -----------------------------------------------------------------


def test_limiter_allows_up_to_the_limit_then_refuses(clock):
    limiter = SlidingWindowLimiter(3, timedelta(minutes=15), clock)
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(TooManyAttempts):
        limiter.check("1.2.3.4")
    limiter.check("5.6.7.8")  # other addresses are unaffected


def test_limiter_window_slides(clock):
    limiter = SlidingWindowLimiter(2, timedelta(minutes=15), clock)
    limiter.check("a")
    clock.advance(minutes=10)
    limiter.check("a")
    with pytest.raises(TooManyAttempts):
        limiter.check("a")
    clock.advance(minutes=6)  # the first attempt has left the window
    limiter.check("a")


def test_limiter_memory_is_bounded(clock):
    limiter = SlidingWindowLimiter(1, timedelta(minutes=15), clock)
    for i in range(MAX_TRACKED_CLIENTS + 5):
        limiter.check(f"10.0.{i // 256}.{i % 256}")
    assert len(limiter._events) <= MAX_TRACKED_CLIENTS


def test_limiter_rejects_a_zero_limit(clock):
    with pytest.raises(ValueError, match="at least 1"):
        SlidingWindowLimiter(0, timedelta(minutes=1), clock)


# --- over HTTP ------------------------------------------------------------------------------


def test_sign_in_is_rate_limited_per_address_across_all_names(tmp_path, clock, sender):
    client = _limited_client(tmp_path, clock, sender, attempts=3)
    statuses = [_try_sign_in(client, "ABCD-EFGH", f"Guess {i}", "wrong-pass") for i in range(4)]
    assert statuses == [401, 401, 401, 429]
    page = client.get("/login")
    assert page.status_code == 200  # viewing the form is never limited


def test_rate_limited_page_explains_what_to_do(tmp_path, clock, sender):
    client = _limited_client(tmp_path, clock, sender, attempts=1)
    _try_sign_in(client, "ABCD-EFGH", "A", "wrong-pass")
    token = csrf_from(client, "/login")
    response = client.post(
        "/login",
        data={"circle_code": "x", "display_name": "y", "passcode": "z", "csrf_token": token},
    )
    assert response.status_code == 429
    assert "Too many attempts from this network" in response.text
    assert "wrong-pass" not in response.text and 'value="z"' not in response.text


def test_limit_resets_after_the_window(tmp_path, clock, sender):
    client = _limited_client(tmp_path, clock, sender, attempts=1)
    assert _try_sign_in(client, "ABCD-EFGH", "A", "wrong-pass") == 401
    assert _try_sign_in(client, "ABCD-EFGH", "A", "wrong-pass") == 429
    clock.advance(minutes=16)
    assert _try_sign_in(client, "ABCD-EFGH", "A", "wrong-pass") == 401


def test_starting_circles_is_rate_limited(tmp_path, clock, sender):
    client = _limited_client(tmp_path, clock, sender, attempts=1)
    form = {
        "elder_name": "Asha",
        "passcode": ELDER_PASSCODE,
        "confirm_passcode": ELDER_PASSCODE,
        "email": "",
        "timezone": "Asia/Kolkata",
    }
    first = client.post("/start", data={**form, "csrf_token": csrf_from(client, "/start")})
    assert first.status_code == 303
    client.cookies.clear()
    second = client.post("/start", data={**form, "csrf_token": csrf_from(client, "/start")})
    assert second.status_code == 429


# --- caching, compression, transport security -----------------------------------------------


def test_pages_link_content_hashed_assets_that_are_cached_for_a_year(services):
    client = make_client(services)
    page = client.get("/login")
    match = re.search(r'href="(/static/careloop\.css\?v=[0-9a-f]{12})"', page.text)
    assert match, "stylesheet link should carry a content hash"
    asset = client.get(match.group(1))
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == STATIC_CACHE_VERSIONED
    assert page.headers["cache-control"] == "no-store"  # pages hold health data


def test_unversioned_static_urls_are_cached_briefly(services):
    response = make_client(services).get("/static/careloop.css")
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_large_responses_are_gzip_compressed(services):
    response = make_client(services).get(
        "/static/careloop.css", headers={"Accept-Encoding": "gzip"}
    )
    assert response.headers.get("content-encoding") == "gzip"


def test_hsts_is_sent_only_over_https(services):
    app = create_app(services.ctx.settings, services)
    plain = TestClient(app).get("/login")
    secure = TestClient(app, base_url="https://testserver").get("/login")
    assert "strict-transport-security" not in plain.headers
    assert secure.headers["strict-transport-security"].startswith("max-age=31536000")
