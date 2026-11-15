"""The escalation ladder for an unresolved dose.

    scheduled time            -> remind the elder           (level 1)
    + caretaker_after minutes -> notify caretakers          (level 2)
    + family_after minutes    -> notify family              (level 3)
    + close_after minutes     -> stop and mark UNCONFIRMED  (outcome unknown)

Resolving the dose (taken or skipped) at any point stops the ladder.

`plan_escalation` is a pure function. The care cycle calls it, then asks the
repository to apply the step only if the dose is still in the state it read,
so a confirmation that lands mid-cycle always wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import (
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Member,
    NotificationKind,
    Role,
)

MAX_CLOSE_AFTER_MINUTES = 12 * 60
MIN_CARETAKER_MINUTES = 5


class EscalationAction(str, Enum):
    """What the care cycle should do with one dose right now."""

    NONE = "none"
    ADVANCE = "advance"
    CLOSE = "close"


@dataclass(frozen=True)
class EscalationDecision:
    """The result of `plan_escalation`: an action and, to advance, the new level."""

    action: EscalationAction
    to_level: EscalationLevel | None = None


NO_ACTION = EscalationDecision(EscalationAction.NONE)


def validate_policy(policy: EscalationPolicy) -> EscalationPolicy:
    """Steps must happen in order and the ladder must end within 12 hours."""
    errors: dict[str, str] = {}
    if policy.caretaker_after_minutes < MIN_CARETAKER_MINUTES:
        errors["caretaker_after_minutes"] = f"Must be at least {MIN_CARETAKER_MINUTES} minutes."
    if policy.family_after_minutes <= policy.caretaker_after_minutes:
        errors["family_after_minutes"] = "Must be later than the caretaker step."
    if policy.close_after_minutes <= policy.family_after_minutes:
        errors["close_after_minutes"] = "Must be later than the family step."
    if policy.close_after_minutes > MAX_CLOSE_AFTER_MINUTES:
        errors["close_after_minutes"] = "Must be 12 hours (720 minutes) or less."
    if errors:
        raise ValidationFailed(errors)
    return policy


def target_level(elapsed: timedelta, policy: EscalationPolicy) -> EscalationLevel:
    """The highest level whose waiting time has passed after `elapsed`."""
    if elapsed >= timedelta(minutes=policy.family_after_minutes):
        return EscalationLevel.FAMILY_NOTIFIED
    if elapsed >= timedelta(minutes=policy.caretaker_after_minutes):
        return EscalationLevel.CARETAKER_NOTIFIED
    if elapsed >= timedelta(0):
        return EscalationLevel.ELDER_REMINDED
    return EscalationLevel.NONE


def plan_escalation(dose: Dose, policy: EscalationPolicy, now: datetime) -> EscalationDecision:
    """Decide the next step for one dose.

    At most one level is climbed per call. If the scheduler was down long
    enough that the whole window has passed, the dose is closed without
    sending stale alerts.
    """
    if dose.status is not DoseStatus.SCHEDULED:
        return NO_ACTION
    elapsed = now - dose.scheduled_for
    if elapsed < timedelta(0):
        return NO_ACTION
    if elapsed >= timedelta(minutes=policy.close_after_minutes):
        return EscalationDecision(EscalationAction.CLOSE)
    if target_level(elapsed, policy) > dose.escalation_level:
        return EscalationDecision(
            EscalationAction.ADVANCE, EscalationLevel(dose.escalation_level + 1)
        )
    return NO_ACTION


LEVEL_AUDIENCE: dict[EscalationLevel, tuple[Role, NotificationKind]] = {
    EscalationLevel.ELDER_REMINDED: (Role.ELDER, NotificationKind.DOSE_REMINDER),
    EscalationLevel.CARETAKER_NOTIFIED: (
        Role.CARETAKER,
        NotificationKind.DOSE_ESCALATION_CARETAKER,
    ),
    EscalationLevel.FAMILY_NOTIFIED: (Role.FAMILY, NotificationKind.DOSE_ESCALATION_FAMILY),
}


def recipients_for_level(level: EscalationLevel, members: list[Member]) -> list[Member]:
    """Active members who should hear about a dose at this level."""
    if level not in LEVEL_AUDIENCE:
        return []
    role, _ = LEVEL_AUDIENCE[level]
    return [m for m in members if m.active and m.role is role]


def notification_kind_for_level(level: EscalationLevel) -> NotificationKind:
    """The kind of email sent when a dose reaches `level`."""
    return LEVEL_AUDIENCE[level][1]
