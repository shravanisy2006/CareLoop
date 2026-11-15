"""End-to-end escalation through the real services and a real SQLite database."""

from __future__ import annotations

from careloop.domain.errors import AlreadyResolved
from careloop.domain.models import DoseStatus, EscalationLevel, NotificationStatus
from tests.conftest import medication_details


def _setup_dose(services, circle):
    services.records.add_medication(circle.elder, medication_details())
    dose = services.doses.today(circle.elder, services.circles.get_circle(circle.elder))[0].dose
    return dose


def test_escalation_climbs_elder_then_caretaker_then_family(services, circle, clock, sender):
    dose = _setup_dose(services, circle)
    assert dose.status is DoseStatus.SCHEDULED

    clock.set(dose.scheduled_for)  # 08:00 local: remind the elder
    services.cycle.run_once()
    assert [m.to for m in sender.sent] == ["asha@example.com"]

    clock.advance(minutes=30)  # caretaker step (default 30 min)
    services.cycle.run_once()
    assert sender.to("ravi@example.com"), "caretaker should be notified"
    assert not sender.to("meera@example.com")

    clock.advance(minutes=30)  # family step (default 60 min)
    services.cycle.run_once()
    assert sender.to("meera@example.com"), "family should be notified"
    assert services.ctx.repo.get_dose(dose.id).escalation_level is EscalationLevel.FAMILY_NOTIFIED


def test_confirming_stops_further_escalation(services, circle, clock, sender):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    services.cycle.run_once()  # elder reminded

    clock.advance(minutes=5)
    services.doses.record(circle.elder, dose.id, "taken")

    clock.advance(hours=3)
    for _ in range(3):
        services.cycle.run_once()
    recipients = {m.to for m in sender.sent}
    assert recipients == {"asha@example.com"}
    stored = services.ctx.repo.get_dose(dose.id)
    assert stored.status is DoseStatus.TAKEN
    assert stored.resolved_at is not None and stored.resolved_at > dose.scheduled_for
    assert stored.resolved_by == circle.elder.id


def test_repeated_cycles_never_send_duplicate_emails(services, circle, clock, sender):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    for _ in range(5):
        services.cycle.run_once()
    assert len(sender.to("asha@example.com")) == 1


def test_window_closes_as_unconfirmed_not_missed(services, circle, clock):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    for minutes in (0, 30, 30, 120):
        clock.advance(minutes=minutes)
        services.cycle.run_once()
    assert services.ctx.repo.get_dose(dose.id).status is DoseStatus.UNCONFIRMED


def test_late_answer_still_recorded_after_window_closed(services, circle, clock):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    clock.advance(hours=4)
    services.cycle.run_once()  # long outage: closes without stale alerts
    assert services.ctx.repo.get_dose(dose.id).status is DoseStatus.UNCONFIRMED

    services.doses.record(circle.caretaker, dose.id, "taken")
    stored = services.ctx.repo.get_dose(dose.id)
    assert stored.status is DoseStatus.TAKEN
    assert stored.resolved_by == circle.caretaker.id


def test_duplicate_confirmation_is_rejected(services, circle, clock):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    services.doses.record(circle.elder, dose.id, "taken")
    try:
        services.doses.record(circle.elder, dose.id, "taken")
    except AlreadyResolved:
        pass
    else:
        raise AssertionError("second confirmation should be rejected")


def test_email_failure_is_retried_then_recorded(services, circle, clock, sender):
    dose = _setup_dose(services, circle)
    clock.set(dose.scheduled_for)
    sender.failures_remaining = 1
    services.cycle.run_once()
    [notification] = services.history.notifications(circle.elder)
    assert notification.status is NotificationStatus.FAILED
    assert notification.attempts == 1

    services.cycle.run_once()  # retry succeeds
    [notification] = services.history.notifications(circle.elder)
    assert notification.status is NotificationStatus.SENT
    assert notification.attempts == 2
