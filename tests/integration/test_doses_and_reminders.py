"""Dose generation, recording rules, races, and appointment reminders."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from careloop.domain.changes import audit_event
from careloop.domain.errors import AlreadyResolved, InvalidStateTransition, NotFound
from careloop.domain.models import DoseStatus, EscalationLevel, NotificationKind, Role
from careloop.domain.validation import validate_appointment_input
from tests.conftest import medication_details


def circle_of(services, member):
    return services.circles.get_circle(member)


def test_dose_generation_is_idempotent(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details(times="08:00, 20:00"))
    the_circle = circle_of(services, circle.elder)
    assert services.doses.generate_for_circle(the_circle, clock.now()) == 0  # already created
    doses = services.ctx.repo.list_doses(
        the_circle.id, clock.now(), clock.now() + timedelta(days=3)
    )
    assert len(doses) == len({d.scheduled_for for d in doses}) == 4


def test_new_medicine_has_no_backdated_doses(services, circle, clock):
    clock.advance(hours=6)  # 13:30 local; today's 08:00 has passed
    services.records.add_medication(circle.elder, medication_details())
    the_circle = circle_of(services, circle.elder)
    doses = services.ctx.repo.list_doses(
        the_circle.id, clock.now() - timedelta(days=1), clock.now()
    )
    assert doses == []


def test_stopped_medicine_cancels_upcoming_doses(services, circle, clock):
    med_id = services.records.add_medication(circle.elder, medication_details())
    services.records.stop_medication(circle.elder, med_id)
    the_circle = circle_of(services, circle.elder)
    doses = services.ctx.repo.list_doses(
        the_circle.id, clock.now(), clock.now() + timedelta(days=3)
    )
    assert doses and all(d.status is DoseStatus.CANCELLED for d in doses)
    assert services.doses.generate_for_circle(the_circle, clock.now()) == 0


def test_caretaker_can_record_on_behalf_and_it_is_attributed(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    clock.set(view.dose.scheduled_for + timedelta(minutes=40))
    dose = services.doses.record(circle.caretaker, view.dose.id, "taken")
    assert dose.resolved_by == circle.caretaker.id and dose.resolved_by_role is Role.CARETAKER
    event = next(e for e in services.history.audit_events(circle.elder) if e.action == "dose.taken")
    assert event.after["on_behalf_of_elder"] is True


def test_dose_cannot_be_recorded_long_before_it_is_due(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details(times="20:00"))
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    with pytest.raises(InvalidStateTransition):
        services.doses.record(circle.elder, view.dose.id, "taken")


def test_recording_requires_a_valid_outcome(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    clock.set(view.dose.scheduled_for)
    with pytest.raises(InvalidStateTransition):
        services.doses.record(circle.elder, view.dose.id, "unconfirmed")


def test_cannot_record_a_dose_from_another_circle(services, circle, clock):
    _, other_elder = services.circles.create_circle(
        elder_name="Other",
        passcode="other-pass-99",
        confirm_passcode="other-pass-99",
        email="",
        timezone="Asia/Kolkata",
    )
    services.records.add_medication(other_elder, medication_details())
    [view] = services.doses.today(other_elder, circle_of(services, other_elder))
    clock.set(view.dose.scheduled_for)
    with pytest.raises(NotFound):
        services.doses.record(circle.elder, view.dose.id, "taken")


def test_confirmation_during_escalation_wins(services, circle, clock, sender):
    """The cycle reads the dose, then the elder confirms, then the cycle tries
    to escalate. The conditional update must refuse, and nothing is queued."""
    services.records.add_medication(circle.elder, medication_details())
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    clock.set(view.dose.scheduled_for + timedelta(minutes=31))
    stale_read = services.ctx.repo.get_dose(view.dose.id)

    services.doses.record(circle.elder, view.dose.id, "taken")

    audit = audit_event(circle.elder, "dose.escalated", "dose", view.dose.id, clock.now())
    applied = services.ctx.repo.advance_escalation(
        stale_read.id, stale_read.escalation_level, EscalationLevel.ELDER_REMINDED, [], audit
    )
    assert applied is False
    services.cycle.run_once()
    assert sender.sent == []


def test_queued_alert_is_cancelled_if_dose_is_confirmed_before_sending(
    services, circle, clock, sender
):
    services.records.add_medication(circle.elder, medication_details())
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    clock.set(view.dose.scheduled_for)
    sender.failures_remaining = 1  # first send fails, so the reminder stays queued
    services.cycle.run_once()
    services.doses.record(circle.elder, view.dose.id, "taken")
    services.cycle.run_once()
    assert sender.sent == []
    [note] = services.history.notifications(circle.elder)
    assert note.status.value == "cancelled"


def test_simultaneous_confirmations_record_exactly_once(services, circle, clock):
    services.records.add_medication(circle.elder, medication_details())
    [view] = services.doses.today(circle.elder, circle_of(services, circle.elder))
    clock.set(view.dose.scheduled_for)
    results: list[str] = []

    def confirm(member):
        try:
            services.doses.record(member, view.dose.id, "taken")
            results.append("ok")
        except AlreadyResolved:
            results.append("already")

    threads = [
        threading.Thread(target=confirm, args=(m,))
        for m in (circle.elder, circle.caretaker, circle.elder, circle.caretaker)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["already", "already", "already", "ok"]
    confirmations = [
        e for e in services.history.audit_events(circle.elder) if e.action == "dose.taken"
    ]
    assert len(confirmations) == 1


def test_appointment_reminder_is_queued_once_within_24_hours(services, circle, clock, sender):
    details = validate_appointment_input(
        doctor_name="Dr. Shah",
        purpose="Check-up",
        location="",
        date_value="2026-11-17",
        time_value="10:00",
        notes="",
        tz_name="Asia/Kolkata",
        now=clock.now(),
    )
    services.records.add_appointment(circle.elder, details)
    services.cycle.run_once()
    assert sender.sent == []  # more than 24 hours away

    clock.advance(hours=27)
    services.cycle.run_once()
    services.cycle.run_once()
    reminders = [
        n
        for n in services.history.notifications(circle.elder)
        if n.kind is NotificationKind.APPOINTMENT_REMINDER
    ]
    assert len(reminders) == 3  # one per member
    assert len(sender.sent) == 3
    assert "calendar.google.com" in sender.sent[0].body


def test_unconfigured_email_is_recorded_honestly(settings, clock, tmp_path):
    from careloop.notifications.email_sender import UnconfiguredSender
    from careloop.services.container import build_services
    from careloop.storage.sqlite_repository import SqliteRepository

    services = build_services(
        settings,
        repo=SqliteRepository(tmp_path / "x.db"),
        clock=clock,
        email_sender=UnconfiguredSender(),
    )
    _, elder = services.circles.create_circle(
        elder_name="Asha",
        passcode="river-blue-42",
        confirm_passcode="river-blue-42",
        email="asha@example.com",
        timezone="Asia/Kolkata",
    )
    services.records.add_medication(elder, medication_details())
    clock.advance(minutes=31)
    services.cycle.run_once()
    [note] = services.history.notifications(elder)
    assert note.status.value == "not_configured" and note.sent_at is None
