"""Input validation returns clean values or plain-language field errors."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from careloop.domain.errors import ValidationFailed
from careloop.domain.validation import (
    validate_appointment_input,
    validate_circle_code,
    validate_email,
    validate_medication_input,
    validate_passcode,
    validate_policy_input,
)

NOW = datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)


def med(**overrides):
    values = dict(
        name="Metformin",
        dosage="1 tablet",
        instructions="",
        times="20:00, 08:00",
        start_date="2026-11-15",
        end_date="",
    )
    values.update(overrides)
    return validate_medication_input(**values)


def errors_of(fn, **kwargs) -> dict[str, str]:
    with pytest.raises(ValidationFailed) as err:
        fn(**kwargs)
    return err.value.field_errors


def test_valid_medication_is_cleaned_and_times_sorted():
    result = med(name="  Metformin\u0000 ", times=["20:00", "08:00", "08:00", ""])
    assert result.name == "Metformin"
    assert result.times == ("08:00", "20:00")
    assert result.start_date == date(2026, 11, 15)


def test_medication_requires_name_dosage_and_a_time():
    errors = errors_of(med, name="", dosage="", times="")
    assert set(errors) == {"name", "dosage", "times"}


def test_medication_rejects_bad_times_and_end_before_start():
    errors = errors_of(med, times="25:00", end_date="2026-11-01")
    assert set(errors) == {"times", "end_date"}


def test_medication_limits_doses_per_day_and_text_length():
    times = [f"{h:02d}:00" for h in range(9)]
    assert "times" in errors_of(med, times=times)
    assert "name" in errors_of(med, name="x" * 101)


def appt(**overrides):
    values = dict(
        doctor_name="Dr. Shah",
        purpose="Check-up",
        location="",
        date_value="2026-11-20",
        time_value="10:30",
        notes="",
        tz_name="Asia/Kolkata",
        now=NOW,
    )
    values.update(overrides)
    return validate_appointment_input(**values)


def test_appointment_time_is_stored_in_utc():
    assert appt().starts_at == datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)


def test_appointment_must_be_in_the_future_and_within_two_years():
    assert "date" in errors_of(appt, date_value="2026-11-14")
    assert "date" in errors_of(appt, date_value="2030-01-01")


def test_appointment_rejects_malformed_date_and_time():
    assert set(errors_of(appt, date_value="20/11/2026", time_value="10.30")) == {"date", "time"}


@pytest.mark.parametrize("weak", ["12345", "111111", "123456", "987654", ""])
def test_weak_passcodes_are_rejected(weak):
    with pytest.raises(ValidationFailed):
        validate_passcode(weak)


def test_reasonable_passcode_is_accepted():
    assert validate_passcode("river-blue-42") == "river-blue-42"


def test_email_is_normalised_and_checked():
    assert validate_email("  Asha@Example.COM ") == "asha@example.com"
    assert validate_email("") is None
    with pytest.raises(ValidationFailed):
        validate_email("asha@")


def test_circle_code_accepts_display_format():
    assert validate_circle_code("abcd-2345") == "ABCD2345"
    with pytest.raises(ValidationFailed):
        validate_circle_code("ABC'; DROP")


def test_policy_input_must_be_whole_numbers():
    with pytest.raises(ValidationFailed):
        validate_policy_input("thirty", "60", "180")
    assert validate_policy_input("15", "45", "120").family_after_minutes == 45
