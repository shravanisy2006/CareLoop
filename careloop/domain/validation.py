"""Input validation for everything a person can type into CareLoop.

Each validator either returns a clean domain value or raises ValidationFailed
with one plain-language message per field, which the web layer shows next to
the field.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from careloop.domain.errors import ValidationFailed
from careloop.domain.escalation import validate_policy
from careloop.domain.models import (
    Advice,
    AdviceSource,
    AppointmentDetails,
    EscalationPolicy,
    MedicationDetails,
    Role,
)
from careloop.domain.schedule import parse_local_time

MAX_DOSE_TIMES_PER_DAY = 8
MAX_NAME_LENGTH = 100
MIN_PASSCODE_LENGTH = 6
MAX_PASSCODE_LENGTH = 64
MAX_EMAIL_LENGTH = 254  # RFC 5321 limit for a forward path
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
CIRCLE_CODE_PATTERN = re.compile(r"^[A-Z0-9]{8}$")


def clean_text(value: str | None, *, max_length: int) -> str:
    """Normalise unicode, drop control characters and trim whitespace."""
    text = unicodedata.normalize("NFKC", value or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return text.strip()[: max_length + 1]


def _required(errors: dict[str, str], field: str, value: str, label: str, max_length: int) -> str:
    if not value:
        errors[field] = f"Please enter {label}."
    elif len(value) > max_length:
        errors[field] = f"Please keep this under {max_length} characters."
    return value


def _optional(errors: dict[str, str], field: str, value: str, max_length: int) -> str:
    if len(value) > max_length:
        errors[field] = f"Please keep this under {max_length} characters."
    return value


def _parse_date(errors: dict[str, str], field: str, raw: str, *, required: bool) -> date | None:
    raw = raw.strip()
    if not raw:
        if required:
            errors[field] = "Please enter a date."
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[field] = "Please enter a date like 2026-11-15."
        return None


def parse_times(raw: str | list[str]) -> tuple[str, ...]:
    """Accept "08:00, 20:00" or a list. Returns sorted unique "HH:MM" values."""
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", raw)
    values = {parse_local_time(item.strip()).strftime("%H:%M") for item in items if item.strip()}
    return tuple(sorted(values))


def validate_medication_input(
    *,
    name: str,
    dosage: str,
    instructions: str,
    times: str | list[str],
    start_date: str,
    end_date: str,
) -> MedicationDetails:
    """Validate a medication form and return clean MedicationDetails."""
    errors: dict[str, str] = {}
    clean_name = _required(
        errors, "name", clean_text(name, max_length=100), "the medicine name", 100
    )
    clean_dosage = _required(
        errors, "dosage", clean_text(dosage, max_length=100), "the dose (for example 1 tablet)", 100
    )
    clean_instructions = _optional(
        errors, "instructions", clean_text(instructions, max_length=500), 500
    )

    parsed_times: tuple[str, ...] = ()
    try:
        parsed_times = parse_times(times)
        if not parsed_times:
            errors["times"] = "Please enter at least one time, like 08:00."
        elif len(parsed_times) > MAX_DOSE_TIMES_PER_DAY:
            errors["times"] = f"Please enter at most {MAX_DOSE_TIMES_PER_DAY} times a day."
    except ValueError:
        errors["times"] = "Please enter times like 08:00, 20:30 (24-hour clock)."

    start = _parse_date(errors, "start_date", start_date, required=True)
    end = _parse_date(errors, "end_date", end_date, required=False)
    if start and end and end < start:
        errors["end_date"] = "The end date cannot be before the start date."

    if errors or start is None:
        raise ValidationFailed(errors or {"start_date": "Please enter a date."})
    return MedicationDetails(
        name=clean_name,
        dosage=clean_dosage,
        instructions=clean_instructions,
        times=parsed_times,
        start_date=start,
        end_date=end,
    )


def validate_appointment_input(
    *,
    doctor_name: str,
    purpose: str,
    location: str,
    date_value: str,
    time_value: str,
    notes: str,
    tz_name: str,
    now: datetime,
) -> AppointmentDetails:
    """Appointments must be in the future and within two years."""
    errors: dict[str, str] = {}
    clean_doctor = _required(
        errors, "doctor_name", clean_text(doctor_name, max_length=100), "the doctor's name", 100
    )
    clean_purpose = _required(
        errors, "purpose", clean_text(purpose, max_length=200), "what the visit is for", 200
    )
    clean_location = _optional(errors, "location", clean_text(location, max_length=200), 200)
    clean_notes = _optional(errors, "notes", clean_text(notes, max_length=500), 500)

    day = _parse_date(errors, "date", date_value, required=True)
    local_time = None
    try:
        local_time = parse_local_time(time_value.strip())
    except ValueError:
        errors["time"] = "Please enter a time like 10:30 (24-hour clock)."

    starts_at: datetime | None = None
    if day and local_time:
        starts_at = datetime.combine(day, local_time, tzinfo=ZoneInfo(tz_name)).astimezone(
            timezone.utc
        )
        if starts_at <= now:
            errors["date"] = "The appointment must be in the future."
        elif starts_at > now + timedelta(days=730):
            errors["date"] = "Please choose a date within the next two years."

    if errors or starts_at is None:
        raise ValidationFailed(errors or {"date": "Please enter a date."})
    return AppointmentDetails(
        doctor_name=clean_doctor,
        purpose=clean_purpose,
        location=clean_location,
        starts_at=starts_at,
        notes=clean_notes,
    )


def validate_passcode(passcode: str, field: str = "passcode") -> str:
    """At least 6 characters, and not a trivially guessable pattern."""
    value = passcode or ""
    message = None
    if len(value) < MIN_PASSCODE_LENGTH:
        message = f"Please use at least {MIN_PASSCODE_LENGTH} characters."
    elif len(value) > MAX_PASSCODE_LENGTH:
        message = f"Please use {MAX_PASSCODE_LENGTH} characters or fewer."
    elif len(set(value)) == 1 or value in "01234567890" or value in "9876543210":
        message = "This passcode is too easy to guess. Please choose another."
    if message:
        raise ValidationFailed({field: message})
    return value


def validate_display_name(name: str, field: str = "display_name") -> str:
    """Validate a person's name (required, at most 60 characters)."""
    errors: dict[str, str] = {}
    value = _required(errors, field, clean_text(name, max_length=60), "a name", 60)
    if errors:
        raise ValidationFailed(errors)
    return value


def validate_email(email: str, field: str = "email", *, required: bool = False) -> str | None:
    """Validate and lower-case an email address; empty means None unless required."""
    value = clean_text(email, max_length=MAX_EMAIL_LENGTH).lower()
    if not value:
        if required:
            raise ValidationFailed({field: "Please enter an email address."})
        return None
    if len(value) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(value):
        raise ValidationFailed({field: "Please enter a valid email address."})
    return value


def validate_timezone(tz_name: str) -> str:
    """Accept only IANA timezone names such as Asia/Kolkata."""
    if tz_name not in available_timezones():
        raise ValidationFailed({"timezone": "Please choose a valid timezone."})
    return tz_name


def validate_reason(reason: str) -> str:
    """Validate an optional free-text reason or note."""
    errors: dict[str, str] = {}
    value = _optional(errors, "reason", clean_text(reason, max_length=500), 500)
    if errors:
        raise ValidationFailed(errors)
    return value


def validate_circle_code(code: str) -> str:
    """Normalise a typed circle code (dashes, spaces, case) and check its format."""
    value = clean_text(code, max_length=20).upper().replace("-", "").replace(" ", "")
    if not CIRCLE_CODE_PATTERN.match(value):
        raise ValidationFailed({"circle_code": "Care circle codes are 8 letters and numbers."})
    return value


def validate_policy_input(caretaker: str, family: str, close: str) -> EscalationPolicy:
    """Parse and validate the three escalation timings from a form."""
    errors: dict[str, str] = {}
    values: dict[str, int] = {}
    for field, raw in (
        ("caretaker_after_minutes", caretaker),
        ("family_after_minutes", family),
        ("close_after_minutes", close),
    ):
        try:
            values[field] = int(str(raw).strip())
        except ValueError:
            errors[field] = "Please enter a whole number of minutes."
    if errors:
        raise ValidationFailed(errors)
    return validate_policy(EscalationPolicy(**values))


def validate_advice(source: str, advised_by: str, advised_on: str, *, today: date) -> Advice:
    """Who advised a suggested change.

    A doctor's or pharmacist's name is required when they are given as the source, and the date
    cannot be in the future.
    """
    errors: dict[str, str] = {}
    try:
        chosen = AdviceSource(source)
    except ValueError:
        errors["advice_source"] = "Please say who advised this change."
        chosen = AdviceSource.CARE_TEAM
    name = clean_text(advised_by, max_length=MAX_NAME_LENGTH)
    if len(name) > MAX_NAME_LENGTH:
        errors["advised_by"] = f"Please keep this under {MAX_NAME_LENGTH} characters."
    elif chosen in (AdviceSource.DOCTOR, AdviceSource.PHARMACIST) and not name:
        errors["advised_by"] = f"Please enter the {chosen.value}'s name."
    when = _parse_date(errors, "advised_on", advised_on, required=False)
    if when and when > today:
        errors["advised_on"] = "This date cannot be in the future."
    if errors:
        raise ValidationFailed(errors)
    return Advice(source=chosen, advised_by=name, advised_on=when)


def validate_all(*checks: Callable[[], Any]) -> list[Any]:
    """Run every validator and return their results in order.

    Raises one ValidationFailed listing the errors of all of them, so a form shows
    every problem at once instead of one per submit.
    """
    errors: dict[str, str] = {}
    results: list[Any] = []
    for check in checks:
        try:
            results.append(check())
        except ValidationFailed as exc:
            errors.update(exc.field_errors)
    if errors:
        raise ValidationFailed(errors)
    return results


@dataclass(frozen=True)
class PersonInput:
    """Validated details for a new member of a care circle."""

    display_name: str
    email: str | None
    passcode: str


def validate_person(display_name: str, email: str, passcode: str) -> PersonInput:
    """Validate the name, email and passcode of a new member together."""
    name, clean_email, clean_passcode = validate_all(
        lambda: validate_display_name(display_name),
        lambda: validate_email(email),
        lambda: validate_passcode(passcode),
    )
    return PersonInput(name, clean_email, clean_passcode)


def validate_matching_passcodes(passcode: str, confirm_passcode: str) -> None:
    """Check that the passcode was typed the same way twice."""
    if passcode != confirm_passcode:
        raise ValidationFailed({"confirm_passcode": "The two passcodes do not match."})


def validate_helper_role(role: str) -> Role:
    """Helpers are caretakers or family; a circle has exactly one elder."""
    try:
        chosen = Role(role)
    except ValueError:
        chosen = Role.ELDER
    if chosen is Role.ELDER:
        raise ValidationFailed({"role": "Please choose caretaker or family member."})
    return chosen
