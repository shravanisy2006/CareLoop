"""Adherence statistics calculated from real dose records.

Unconfirmed doses are counted separately as "unknown". They are never folded
into either "taken" or "missed", because nobody recorded what happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from careloop.domain.models import Dose, DoseStatus


@dataclass(frozen=True)
class AdherenceSummary:
    """Counts of dose outcomes over a period, with unknown kept separate."""

    taken: int
    taken_on_time: int
    skipped: int
    unconfirmed: int
    awaiting_answer: int  # due, still within the follow-up window

    @property
    def closed_total(self) -> int:
        """Doses whose follow-up window is over (or that were answered)."""
        return self.taken + self.skipped + self.unconfirmed

    @property
    def taken_percent(self) -> int | None:
        """Share of closed doses recorded as taken. None if there is no data."""
        if self.closed_total == 0:
            return None
        return round(100 * self.taken / self.closed_total)

    @property
    def unknown_percent(self) -> int | None:
        """Share of closed doses with no answer recorded. None if there is no data."""
        if self.closed_total == 0:
            return None
        return round(100 * self.unconfirmed / self.closed_total)


def summarise_adherence(doses: list[Dose], now: datetime, on_time_minutes: int) -> AdherenceSummary:
    """Summarise doses that were due at or before `now`.

    A dose counts as "taken on time" if it was recorded as taken within
    `on_time_minutes` of its scheduled time.
    """
    taken = taken_on_time = skipped = unconfirmed = awaiting = 0
    window = timedelta(minutes=on_time_minutes)
    for dose in doses:
        if dose.scheduled_for > now or dose.status is DoseStatus.CANCELLED:
            continue
        if dose.status is DoseStatus.TAKEN:
            taken += 1
            if dose.resolved_at is not None and dose.resolved_at - dose.scheduled_for <= window:
                taken_on_time += 1
        elif dose.status is DoseStatus.SKIPPED:
            skipped += 1
        elif dose.status is DoseStatus.UNCONFIRMED:
            unconfirmed += 1
        else:
            awaiting += 1
    return AdherenceSummary(taken, taken_on_time, skipped, unconfirmed, awaiting)
