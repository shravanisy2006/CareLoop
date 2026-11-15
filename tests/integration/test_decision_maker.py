"""The elder's trusted decision-maker, advice on proposals, and upgrading an
existing database to the new columns."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from careloop.domain.errors import InvalidStateTransition, NotFound, PermissionDenied
from careloop.domain.models import Advice, AdviceSource, ProposalKind, ProposalStatus
from careloop.storage.sqlite_repository import SqliteRepository
from tests.conftest import medication_details

DOCTOR = Advice(AdviceSource.DOCTOR, "Dr. Shah", date(2026, 11, 14))


def _stop_proposal(services, circle, proposer):
    med_id = services.records.add_medication(circle.elder, medication_details())
    return med_id, services.proposals.propose(
        proposer, kind=ProposalKind.STOP_MEDICATION, target_id=med_id, advice=DOCTOR
    )


def test_trusted_helper_can_approve_on_the_elders_behalf(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    _, proposal = _stop_proposal(services, circle, circle.family)
    services.proposals.approve(circle.caretaker, proposal.id, "Discussed with Asha")
    decided = services.ctx.repo.get_proposal(proposal.id)
    assert decided.status is ProposalStatus.APPROVED and decided.decided_by == circle.caretaker.id
    event = next(
        e for e in services.history.audit_events(circle.elder) if e.action == "proposal.approved"
    )
    assert event.after["on_behalf_of_elder"] is True and event.actor_role == "caretaker"


def test_trusted_helper_cannot_approve_their_own_suggestion(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    _, proposal = _stop_proposal(services, circle, circle.caretaker)
    with pytest.raises(PermissionDenied):
        services.proposals.approve(circle.caretaker, proposal.id)
    services.proposals.approve(circle.elder, proposal.id)  # the elder still can


def test_other_helpers_cannot_decide(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    _, proposal = _stop_proposal(services, circle, circle.caretaker)
    with pytest.raises(PermissionDenied):
        services.proposals.reject(circle.family, proposal.id)


def test_elder_can_take_the_trust_back(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    services.circles.set_decision_maker(circle.elder, None)
    _, proposal = _stop_proposal(services, circle, circle.family)
    with pytest.raises(PermissionDenied):
        services.proposals.approve(circle.caretaker, proposal.id)
    actions = [e.action for e in services.history.audit_events(circle.elder)]
    assert actions.count("settings.decision_maker_changed") == 2


def test_removing_the_trusted_helper_clears_the_role(services, circle):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    services.circles.remove_member(circle.elder, circle.caretaker.id)
    assert services.circles.get_circle(circle.elder).decision_maker_id is None


def test_only_the_elder_chooses_and_only_from_their_own_helpers(services, circle):
    with pytest.raises(PermissionDenied):
        services.circles.set_decision_maker(circle.caretaker, circle.caretaker.id)
    with pytest.raises(InvalidStateTransition):
        services.circles.set_decision_maker(circle.elder, circle.elder.id)
    _, other_elder = services.circles.create_circle(
        elder_name="Other",
        passcode="other-pass-99",
        confirm_passcode="other-pass-99",
        email="",
        timezone="Asia/Kolkata",
    )
    with pytest.raises(NotFound):
        services.circles.set_decision_maker(circle.elder, other_elder.id)


def test_trusted_helper_is_emailed_with_a_plain_summary(services, circle, sender):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    _stop_proposal(services, circle, circle.family)
    services.cycle.run_once()
    [to_helper] = sender.to("ravi@example.com")
    [to_elder] = sender.to("asha@example.com")
    assert "trusted you to decide" in to_helper.body
    for email in (to_helper, to_elder):
        assert "Meera suggests stopping Metformin" in email.body
        assert "Advised by Dr. Shah on 14 Nov 2026." in email.body


def test_proposer_is_not_emailed_about_their_own_suggestion(services, circle, sender):
    services.circles.set_decision_maker(circle.elder, circle.caretaker.id)
    _stop_proposal(services, circle, circle.caretaker)
    services.cycle.run_once()
    assert sender.to("ravi@example.com") == []


def test_advice_is_stored_and_shown_in_the_summary(services, circle):
    _, proposal = _stop_proposal(services, circle, circle.caretaker)
    stored = services.ctx.repo.get_proposal(proposal.id)
    assert stored.advice == DOCTOR
    assert services.proposals.summary(circle.elder, stored).endswith(
        "Advised by Dr. Shah on 14 Nov 2026."
    )


def test_existing_database_is_upgraded_in_place(tmp_path):
    """A database created before these features gets the new columns on start-up."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE circles (id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE,
            elder_name TEXT NOT NULL, timezone TEXT NOT NULL,
            caretaker_after_minutes INTEGER NOT NULL, family_after_minutes INTEGER NOT NULL,
            close_after_minutes INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE proposals (id TEXT PRIMARY KEY, circle_id TEXT NOT NULL, kind TEXT NOT NULL,
            target_id TEXT, base_version INTEGER, payload_json TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, proposed_by TEXT NOT NULL,
            proposed_by_role TEXT NOT NULL, created_at TEXT NOT NULL, decided_by TEXT,
            decided_at TEXT, decision_note TEXT NOT NULL DEFAULT '');
        """
    )
    conn.close()
    SqliteRepository(path)
    conn = sqlite3.connect(path)
    circle_columns = {row[1] for row in conn.execute("PRAGMA table_info(circles)")}
    proposal_columns = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    conn.close()
    assert "decision_maker_id" in circle_columns
    assert {"advice_source", "advised_by", "advised_on"} <= proposal_columns
