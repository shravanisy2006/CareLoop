"""Notification content, de-duplication keys and Google Calendar links."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from careloop.domain.models import (
    Appointment,
    AppointmentDetails,
    AppointmentStatus,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Medication,
    MedicationDetails,
    MedicationStatus,
    Member,
    NotificationKind,
    NotificationStatus,
    Role,
)
from careloop.integrations.google_calendar import add_to_calendar_url
from careloop.notifications.messages import dose_notifications, notification_id

NOW = datetime(2026, 11, 15, 2, 30, tzinfo=timezone.utc)
CIRCLE = Circle("c1", "ABCD2345", "Asha", "Asia/Kolkata", EscalationPolicy(), NOW)
MED = Medication(
    "m1",
    "c1",
    MedicationDetails("Metformin", "1 tablet", "", ("08:00",), date(2026, 11, 15), None),
    MedicationStatus.ACTIVE,
    1,
    NOW,
    NOW,
)
DOSE = Dose(
    "d1",
    "c1",
    "m1",
    NOW,
    DoseStatus.SCHEDULED,
    EscalationLevel.ELDER_REMINDED,
    None,
    None,
    None,
    NOW,
)


def person(role: Role, email: str | None) -> Member:
    return Member(
        role.value, "c1", role.value.title(), role, email, "secret-hash", 0, None, True, NOW
    )


def test_notification_ids_are_stable_per_kind_subject_and_recipient():
    a = notification_id(NotificationKind.DOSE_REMINDER, "d1", "m1")
    assert a == notification_id(NotificationKind.DOSE_REMINDER, "d1", "m1")
    assert a != notification_id(NotificationKind.DOSE_REMINDER, "d1", "m2")
    assert a != notification_id(NotificationKind.DOSE_ESCALATION_CARETAKER, "d1", "m1")


def test_caretaker_alert_says_unconfirmed_is_not_missed():
    [note] = dose_notifications(
        CIRCLE,
        DOSE,
        MED,
        EscalationLevel.CARETAKER_NOTIFIED,
        [person(Role.CARETAKER, "ravi@example.com")],
        "http://x",
        NOW,
    )
    assert note.kind is NotificationKind.DOSE_ESCALATION_CARETAKER
    assert "does not mean it was missed" in note.body
    assert "08:00 AM" in note.body  # shown in the circle's local time
    assert "secret-hash" not in note.body


def test_recipient_without_email_is_recorded_as_no_email():
    [note] = dose_notifications(
        CIRCLE,
        DOSE,
        MED,
        EscalationLevel.ELDER_REMINDED,
        [person(Role.ELDER, None)],
        "http://x",
        NOW,
    )
    assert note.status is NotificationStatus.NO_EMAIL


def test_google_calendar_link_has_utc_times_and_title():
    appointment = Appointment(
        "a1",
        "c1",
        AppointmentDetails(
            "Dr. Shah",
            "Check-up",
            "City Clinic",
            datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc),
            "",
        ),
        AppointmentStatus.SCHEDULED,
        1,
        NOW,
        NOW,
    )
    url = urlparse(add_to_calendar_url(appointment, "Asha"))
    query = parse_qs(url.query)
    assert url.netloc == "calendar.google.com"
    assert query["action"] == ["TEMPLATE"]
    assert query["dates"] == ["20261120T050000Z/20261120T060000Z"]
    assert query["text"] == ["Asha: appointment with Dr. Shah"]
    assert query["location"] == ["City Clinic"]
