"""Google Calendar "add event" links for appointments.

This uses Google Calendar's public event-template URL, so anyone in the care
circle can add a visit to their own calendar with one click, with no OAuth
setup and no access to anyone's calendar data.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from urllib.parse import urlencode

from careloop.domain.models import Appointment

GOOGLE_CALENDAR_TEMPLATE_URL = "https://calendar.google.com/calendar/render"
DEFAULT_VISIT_LENGTH = timedelta(hours=1)


def _calendar_time(moment) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def add_to_calendar_url(appointment: Appointment, elder_name: str) -> str:
    d = appointment.details
    start = d.starts_at
    end = start + DEFAULT_VISIT_LENGTH
    params = {
        "action": "TEMPLATE",
        "text": f"{elder_name}: appointment with {d.doctor_name}",
        "dates": f"{_calendar_time(start)}/{_calendar_time(end)}",
        "details": f"Purpose: {d.purpose}",
        "location": d.location,
    }
    return f"{GOOGLE_CALENDAR_TEMPLATE_URL}?{urlencode(params)}"
