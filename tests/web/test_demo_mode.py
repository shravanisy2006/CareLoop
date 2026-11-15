"""Public demo mode: sample circle, one-click sign-in, banner - and none of it
when demo mode is off."""

from __future__ import annotations

import pytest

from careloop.demo import DEMO_CIRCLE_CODE, ensure_demo_circle
from careloop.domain.models import DoseStatus, ProposalStatus
from careloop.services.container import build_services
from careloop.storage.sqlite_repository import SqliteRepository
from tests.conftest import make_settings
from tests.web.helpers import csrf_from, make_client


@pytest.fixture
def demo_services(tmp_path, clock, sender):
    settings = make_settings(tmp_path, demo_mode=True)
    return build_services(
        settings, repo=SqliteRepository(settings.sqlite_path), clock=clock, email_sender=sender
    )


def test_demo_circle_is_created_once_with_useful_content(demo_services):
    first = ensure_demo_circle(demo_services)
    second = ensure_demo_circle(demo_services)
    assert first.id == second.id and first.code == DEMO_CIRCLE_CODE
    repo = demo_services.ctx.repo
    assert len(repo.list_members(first.id)) == 3
    assert len(repo.list_medications(first.id)) == 3
    [proposal] = repo.list_proposals(first.id)
    assert proposal.status is ProposalStatus.PENDING and proposal.advice.advised_by == "Dr. Shah"
    history = repo.list_doses(
        first.id, first.created_at.replace(year=2026, month=11, day=1), first.created_at
    )
    statuses = {d.status for d in history}
    assert {DoseStatus.TAKEN, DoseStatus.SKIPPED, DoseStatus.UNCONFIRMED} <= statuses


@pytest.mark.parametrize(
    ("role", "name"), [("elder", "Asha Rao"), ("caretaker", "Ravi"), ("family", "Meera")]
)
def test_one_click_demo_sign_in(demo_services, role, name):
    client = make_client(demo_services)  # create_app seeds the demo circle
    page = client.get("/login").text
    assert "Try the demo" in page and "Demo version." in page
    token = csrf_from(client, "/login")
    response = client.post("/demo/sign-in", data={"role": role, "csrf_token": token})
    assert response.status_code == 303
    assert f"<strong>{name}</strong>" in client.get("/").text


def test_demo_sign_in_rejects_unknown_roles(demo_services):
    client = make_client(demo_services)
    token = csrf_from(client, "/login")
    assert (
        client.post("/demo/sign-in", data={"role": "admin", "csrf_token": token}).status_code == 404
    )


def test_demo_features_do_not_exist_when_demo_mode_is_off(services):
    client = make_client(services)
    page = client.get("/login").text
    assert "Try the demo" not in page and "Demo version." not in page
    token = csrf_from(client, "/login")
    response = client.post("/demo/sign-in", data={"role": "elder", "csrf_token": token})
    assert response.status_code == 404
    assert services.ctx.repo.get_circle_by_code(DEMO_CIRCLE_CODE) is None
