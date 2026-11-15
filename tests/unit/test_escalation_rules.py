"""The escalation ladder as a pure function of (dose, policy, now)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from careloop.domain.errors import ValidationFailed
from careloop.domain.escalation import (
    EscalationAction,
    plan_escalation,
    recipients_for_level,
    validate_policy,
)
from careloop.domain.models import (
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Member,
    Role,
)

DUE = datetime(2026, 11, 15, 2, 30, tzinfo=timezone.utc)
POLICY = EscalationPolicy(
    caretaker_after_minutes=30, family_after_minutes=60, close_after_minutes=180
)


def dose(level=EscalationLevel.NONE, status=DoseStatus.SCHEDULED) -> Dose:
    return Dose("d1", "c1", "m1", DUE, status, level, None, None, None, DUE)


def at(minutes: float) -> datetime:
    return DUE + timedelta(minutes=minutes)


def test_nothing_happens_before_the_dose_is_due():
    assert plan_escalation(dose(), POLICY, at(-1)).action is EscalationAction.NONE


def test_elder_is_reminded_at_the_scheduled_time():
    decision = plan_escalation(dose(), POLICY, at(0))
    assert decision.action is EscalationAction.ADVANCE
    assert decision.to_level is EscalationLevel.ELDER_REMINDED


def test_caretaker_step_waits_for_grace_period():
    assert (
        plan_escalation(dose(EscalationLevel.ELDER_REMINDED), POLICY, at(29)).action
        is EscalationAction.NONE
    )
    decision = plan_escalation(dose(EscalationLevel.ELDER_REMINDED), POLICY, at(30))
    assert decision.to_level is EscalationLevel.CARETAKER_NOTIFIED


def test_family_step_after_second_interval():
    decision = plan_escalation(dose(EscalationLevel.CARETAKER_NOTIFIED), POLICY, at(60))
    assert decision.to_level is EscalationLevel.FAMILY_NOTIFIED


def test_climbs_only_one_level_per_cycle():
    # Even at 90 minutes, a dose that has had no reminder yet only gets the reminder first.
    decision = plan_escalation(dose(EscalationLevel.NONE), POLICY, at(90))
    assert decision.to_level is EscalationLevel.ELDER_REMINDED


def test_window_end_closes_instead_of_sending_stale_alerts():
    assert (
        plan_escalation(dose(EscalationLevel.NONE), POLICY, at(180)).action
        is EscalationAction.CLOSE
    )


@pytest.mark.parametrize(
    "status", [DoseStatus.TAKEN, DoseStatus.SKIPPED, DoseStatus.UNCONFIRMED, DoseStatus.CANCELLED]
)
def test_resolved_doses_never_escalate(status):
    assert plan_escalation(dose(status=status), POLICY, at(45)).action is EscalationAction.NONE


def test_policy_steps_must_be_in_order():
    with pytest.raises(ValidationFailed) as err:
        validate_policy(EscalationPolicy(30, 20, 10))
    assert {"family_after_minutes", "close_after_minutes"} <= set(err.value.field_errors)


def test_policy_window_is_capped_at_twelve_hours():
    with pytest.raises(ValidationFailed):
        validate_policy(EscalationPolicy(30, 60, 721))
    assert validate_policy(EscalationPolicy(30, 60, 720))


def _member(role: Role, active=True) -> Member:
    return Member(role.value, "c1", role.value, role, None, "x", 0, None, active, DUE)


def test_each_level_reaches_only_its_audience():
    people = [_member(Role.ELDER), _member(Role.CARETAKER), _member(Role.FAMILY)]
    assert [m.role for m in recipients_for_level(EscalationLevel.ELDER_REMINDED, people)] == [
        Role.ELDER
    ]
    assert [m.role for m in recipients_for_level(EscalationLevel.CARETAKER_NOTIFIED, people)] == [
        Role.CARETAKER
    ]
    assert [m.role for m in recipients_for_level(EscalationLevel.FAMILY_NOTIFIED, people)] == [
        Role.FAMILY
    ]


def test_removed_members_are_not_notified():
    assert (
        recipients_for_level(
            EscalationLevel.CARETAKER_NOTIFIED, [_member(Role.CARETAKER, active=False)]
        )
        == []
    )
