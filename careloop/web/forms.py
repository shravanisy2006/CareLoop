"""Turning submitted HTML forms into validated domain values."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from starlette.datastructures import FormData

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import (
    Advice,
    Appointment,
    AppointmentDetails,
    Medication,
    MedicationDetails,
)
from careloop.domain.validation import (
    validate_advice,
    validate_appointment_input,
    validate_medication_input,
)
from careloop.web.rendering import local_datetime

TIME_FIELDS = ("time_1", "time_2", "time_3", "time_4")


def form_values(form: FormData) -> dict[str, str]:
    """Plain dict of submitted values (minus the CSRF token) for re-display."""
    return {k: str(v) for k, v in form.items() if k != "csrf_token"}


def medication_from_form(form: FormData) -> MedicationDetails:
    return validate_medication_input(
        name=str(form.get("name", "")),
        dosage=str(form.get("dosage", "")),
        instructions=str(form.get("instructions", "")),
        times=[str(form.get(f, "")) for f in TIME_FIELDS],
        start_date=str(form.get("start_date", "")),
        end_date=str(form.get("end_date", "")),
    )


def medication_to_values(medication: Medication) -> dict[str, str]:
    d = medication.details
    values = {
        "name": d.name,
        "dosage": d.dosage,
        "instructions": d.instructions,
        "start_date": d.start_date.isoformat(),
        "end_date": d.end_date.isoformat() if d.end_date else "",
    }
    for field, value in zip(TIME_FIELDS, d.times, strict=False):
        values[field] = value
    return values


def appointment_from_form(form: FormData, tz_name: str, now: datetime) -> AppointmentDetails:
    return validate_appointment_input(
        doctor_name=str(form.get("doctor_name", "")),
        purpose=str(form.get("purpose", "")),
        location=str(form.get("location", "")),
        date_value=str(form.get("date", "")),
        time_value=str(form.get("time", "")),
        notes=str(form.get("notes", "")),
        tz_name=tz_name,
        now=now,
    )


def appointment_to_values(appointment: Appointment, tz_name: str) -> dict[str, str]:
    d = appointment.details
    return {
        "doctor_name": d.doctor_name,
        "purpose": d.purpose,
        "location": d.location,
        "date": local_datetime(d.starts_at, tz_name, "%Y-%m-%d"),
        "time": local_datetime(d.starts_at, tz_name, "%H:%M"),
        "notes": d.notes,
    }


def advice_from_form(form: FormData, today: date) -> Advice:
    """Who advised a suggested change (only asked of caretakers and family)."""
    return validate_advice(
        str(form.get("advice_source", "")),
        str(form.get("advised_by", "")),
        str(form.get("advised_on", "")),
        today=today,
    )


def collect_errors(*checks: Callable[[], Any]) -> list[Any]:
    """Run every validator and return their results, or raise one
    ValidationFailed listing every field error at once."""
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
