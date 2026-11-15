"""Per-client rate limiting for the sign-in and sign-up forms.

The per-member lockout in `AuthService` stops guessing one person's passcode. This limiter
complements it by capping how many attempts a single network address can make across *all*
names and circles, so the circle code + name pairs cannot be sprayed.

State is in memory, which matches the single-process deployment. With several processes each one
enforces its own limit; that is documented in SECURITY.md.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta

from starlette.requests import Request

from careloop.clock import Clock
from careloop.domain.errors import CareLoopError

# Upper bound on how many addresses are tracked at once, so memory cannot grow without limit.
MAX_TRACKED_CLIENTS = 10_000


class TooManyAttempts(CareLoopError):
    """One network address made too many sign-in attempts in a short time."""

    status_code = 429
    default_message = (
        "Too many attempts from this network. Please wait a few minutes and try again."
    )


class SlidingWindowLimiter:
    """Allow at most `limit` events per `window` for each key (thread-safe)."""

    def __init__(self, limit: int, window: timedelta, clock: Clock) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._limit = limit
        self._window = window
        self._clock = clock
        self._events: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Record one event for `key`; raise TooManyAttempts if it is over the limit."""
        now = self._clock.now()
        cutoff = now - self._window
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= MAX_TRACKED_CLIENTS:
                    self._forget_idle(cutoff)
                events = self._events[key] = deque()
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                raise TooManyAttempts()
            events.append(now)

    def _forget_idle(self, cutoff: datetime) -> None:
        idle = [key for key, events in self._events.items() if not events or events[-1] <= cutoff]
        for key in idle:
            del self._events[key]
        if len(self._events) >= MAX_TRACKED_CLIENTS:
            # Everyone is active: drop the address that has been quiet longest.
            oldest = min(self._events, key=lambda key: self._events[key][-1])
            del self._events[oldest]


def client_key(request: Request) -> str:
    """The address to rate-limit by (uvicorn's --proxy-headers resolves the real client)."""
    return request.client.host if request.client else "unknown"


def check_sign_in_rate(request: Request) -> None:
    """Apply the app's sign-in limiter to this request."""
    limiter: SlidingWindowLimiter = request.app.state.sign_in_limiter
    limiter.check(client_key(request))
