"""Time source. Services take a Clock so tests can control "now" exactly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """Anything that can tell the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock, used in production."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock that only moves when told to. Used in tests."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._now = start

    def now(self) -> datetime:
        """Return the clock's current (fixed) time."""
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        """Move the clock forward by a timedelta given as keyword arguments."""
        self._now += timedelta(**kwargs)
        return self._now

    def set(self, value: datetime) -> None:
        """Jump the clock to an exact time."""
        self._now = value
