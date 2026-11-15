"""Dose times from schedules, and honest adherence numbers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from careloop.domain.adherence import summarise_adherence
from careloop.domain.models import Dose, DoseStatus, EscalationLevel, MedicationDetails
from careloop.domain.schedule import dose_id_for, dose_times_in_window, parse_local_time

UTC = timezone.utc


def details(times=("08:00", "20:00"), start=date(2026, 11, 15), end=None) -> MedicationDetails:
    return MedicationDetails("Med", "1 tablet", "", tuple(times), start, end)


def test_local_times_are_converted_using_the_circle_timezone():
    window = (datetime(2026, 11, 15, tzinfo=UTC), datetime(2026, 11, 16, tzinfo=UTC))
    times = dose_times_in_window(details(times=("08:00",)), "Asia/Kolkata", *window)
    assert times == [datetime(2026, 11, 15, 2, 30, tzinfo=UTC)]  # 08:00 IST


def test_end_date_is_inclusive_and_nothing_after_it():
    window = (datetime(2026, 11, 14, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC))
    times = dose_times_in_window(details(times=("08:00",), end=date(2026, 11, 16)), "UTC", *window)
    assert [t.day for t in times] == [15, 16]


def test_nothing_before_start_date():
    window = (datetime(2026, 11, 10, tzinfo=UTC), datetime(2026, 11, 15, 12, tzinfo=UTC))
    times = dose_times_in_window(details(), "UTC", *window)
    assert times == [datetime(2026, 11, 15, 8, tzinfo=UTC)]


def test_empty_window_gives_no_doses():
    moment = datetime(2026, 11, 15, tzinfo=UTC)
    assert dose_times_in_window(details(), "UTC", moment, moment) == []


@pytest.mark.parametrize("bad", ["8:00", "24:00", "08:60", "0800", "ab:cd", ""])
def test_invalid_times_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_local_time(bad)


def test_dose_ids_are_deterministic_and_version_scoped():
    moment = datetime(2026, 11, 15, 8, tzinfo=UTC)
    assert dose_id_for("m1", 1, moment) == dose_id_for("m1", 1, moment)
    assert dose_id_for("m1", 1, moment) != dose_id_for("m1", 2, moment)


NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _dose(
    hours_ago: float, status: DoseStatus, answered_after_minutes: float | None = None
) -> Dose:
    scheduled = NOW - timedelta(hours=hours_ago)
    resolved = (
        scheduled + timedelta(minutes=answered_after_minutes)
        if answered_after_minutes is not None
        else None
    )
    return Dose(
        f"d{hours_ago}{status.value}",
        "c",
        "m",
        scheduled,
        status,
        EscalationLevel.NONE,
        resolved,
        "x" if resolved else None,
        None,
        scheduled,
    )


def test_unconfirmed_doses_are_reported_as_unknown_not_missed():
    summary = summarise_adherence(
        [
            _dose(10, DoseStatus.TAKEN, 5),
            _dose(20, DoseStatus.UNCONFIRMED),
            _dose(30, DoseStatus.SKIPPED, 1),
            _dose(40, DoseStatus.TAKEN, 90),
        ],
        NOW,
        on_time_minutes=30,
    )
    assert (summary.taken, summary.skipped, summary.unconfirmed) == (2, 1, 1)
    assert summary.taken_on_time == 1
    assert summary.taken_percent == 50
    assert summary.unknown_percent == 25


def test_future_cancelled_and_open_doses_are_not_counted_as_finished():
    summary = summarise_adherence(
        [
            _dose(-2, DoseStatus.SCHEDULED),
            _dose(5, DoseStatus.CANCELLED),
            _dose(0.5, DoseStatus.SCHEDULED),
        ],
        NOW,
        on_time_minutes=30,
    )
    assert summary.closed_total == 0
    assert summary.awaiting_answer == 1
    assert summary.taken_percent is None
