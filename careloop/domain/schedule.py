"""Turning a medication schedule into concrete dose times.

Pure functions only: given the medication details, the circle's timezone and a
time window, return the UTC instants at which doses fall.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from careloop.domain.models import MedicationDetails


def parse_local_time(value: str) -> time:
    """Parse "HH:MM" (24-hour). Raises ValueError on anything else."""
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise ValueError(f"Time must look like 08:30, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    return time(hour=hour, minute=minute)  # raises ValueError if out of range


def dose_times_in_window(
    details: MedicationDetails,
    tz_name: str,
    window_start: datetime,
    window_end: datetime,
) -> list[datetime]:
    """Return every dose instant (UTC) with window_start <= t < window_end.

    Dates are inclusive of start_date and end_date, read in the circle's
    local timezone.
    """
    if window_end <= window_start:
        return []
    tz = ZoneInfo(tz_name)
    first_day = max(details.start_date, window_start.astimezone(tz).date())
    last_day = window_end.astimezone(tz).date()
    if details.end_date is not None:
        last_day = min(last_day, details.end_date)

    local_times = [parse_local_time(t) for t in details.times]
    result: list[datetime] = []
    day: date = first_day
    while day <= last_day:
        for local_time in local_times:
            instant = datetime.combine(day, local_time, tzinfo=tz).astimezone(timezone.utc)
            if window_start <= instant < window_end:
                result.append(instant)
        day += timedelta(days=1)
    return sorted(result)


def dose_id_for(medication_id: str, medication_version: int, scheduled_for: datetime) -> str:
    """Deterministic dose id, so generating the same window twice cannot
    create duplicate doses. The medication version is part of the id because
    a change cancels future doses and regenerates them from the new details."""
    return f"{medication_id}-v{medication_version}-{int(scheduled_for.timestamp())}"
