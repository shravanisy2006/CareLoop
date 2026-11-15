"""Property-based tests (Hypothesis) for the pure domain rules.

Example-based tests elsewhere pin down specific scenarios. These check invariants
that must hold for *every* valid input: hundreds of generated schedules, policies,
timezones and clock readings per run.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from careloop.domain.adherence import summarise_adherence
from careloop.domain.escalation import (
    MAX_CLOSE_AFTER_MINUTES,
    MIN_CARETAKER_MINUTES,
    EscalationAction,
    plan_escalation,
    target_level,
    validate_policy,
)
from careloop.domain.models import (
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    MedicationDetails,
)
from careloop.domain.schedule import dose_id_for, dose_times_in_window, parse_local_time

TIMEZONES = ["Asia/Kolkata", "Europe/London", "America/New_York", "Australia/Sydney", "UTC"]
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

local_times = st.builds(lambda h, m: f"{h:02d}:{m:02d}", st.integers(0, 23), st.integers(0, 59))
dose_times = st.lists(local_times, min_size=1, max_size=6, unique=True).map(
    lambda ts: tuple(sorted(ts))
)
instants = st.integers(0, 400 * 24 * 60).map(lambda m: BASE + timedelta(minutes=m))


@st.composite
def medications(draw: st.DrawFn) -> MedicationDetails:
    start = draw(st.dates(date(2026, 1, 1), date(2026, 12, 31)))
    end = draw(st.one_of(st.none(), st.dates(start, date(2027, 3, 31))))
    return MedicationDetails("Med", "1 tablet", "", draw(dose_times), start, end)


@st.composite
def policies(draw: st.DrawFn) -> EscalationPolicy:
    caretaker = draw(st.integers(MIN_CARETAKER_MINUTES, 300))
    family = draw(st.integers(caretaker + 1, 600))
    close = draw(st.integers(family + 1, MAX_CLOSE_AFTER_MINUTES).filter(lambda c: c > family))
    return EscalationPolicy(caretaker, family, close)


def _dose(due: datetime, level: EscalationLevel, status: DoseStatus = DoseStatus.SCHEDULED) -> Dose:
    return Dose("d", "c", "m", due, status, level, None, None, None, due)


# --- schedule -------------------------------------------------------------------------------


@given(local_times)
def test_every_valid_time_parses_back_to_itself(value: str) -> None:
    assert parse_local_time(value).strftime("%H:%M") == value


@settings(max_examples=150)
@given(medications(), st.sampled_from(TIMEZONES), instants, st.integers(0, 14 * 24 * 60))
def test_generated_doses_are_sorted_unique_in_window_and_on_schedule(
    med: MedicationDetails, tz_name: str, start: datetime, length: int
) -> None:
    end = start + timedelta(minutes=length)
    doses = dose_times_in_window(med, tz_name, start, end)
    tz = ZoneInfo(tz_name)

    assert doses == sorted(set(doses))
    for instant in doses:
        assert start <= instant < end
        local = instant.astimezone(tz)
        assert med.start_date <= local.date()
        assert med.end_date is None or local.date() <= med.end_date
        assert local.strftime("%H:%M") in med.times or _skipped_by_dst(local, med.times)


def _skipped_by_dst(local: datetime, times: tuple[str, ...]) -> bool:
    """A time inside a spring-forward gap is moved by the timezone; it is still that dose."""
    shifted = (local - timedelta(hours=1)).strftime("%H:%M")
    return shifted in times


@settings(max_examples=100)
@given(medications(), st.sampled_from(TIMEZONES), instants, st.integers(1, 7 * 24 * 60))
def test_splitting_a_window_never_loses_or_duplicates_doses(
    med: MedicationDetails, tz_name: str, start: datetime, length: int
) -> None:
    middle = start + timedelta(minutes=length // 2)
    end = start + timedelta(minutes=length)
    whole = dose_times_in_window(med, tz_name, start, end)
    halves = dose_times_in_window(med, tz_name, start, middle) + dose_times_in_window(
        med, tz_name, middle, end
    )
    assert whole == halves


@given(st.text(min_size=1, max_size=20), st.integers(1, 1000), instants)
def test_dose_ids_are_deterministic(med_id: str, version: int, when: datetime) -> None:
    assert dose_id_for(med_id, version, when) == dose_id_for(med_id, version, when)
    assert dose_id_for(med_id, version, when) != dose_id_for(med_id, version + 1, when)


# --- escalation -----------------------------------------------------------------------------


@given(policies())
def test_every_ordered_policy_within_twelve_hours_is_accepted(policy: EscalationPolicy) -> None:
    assert validate_policy(policy) == policy


@given(policies(), st.integers(-600, MAX_CLOSE_AFTER_MINUTES + 600))
def test_target_level_never_decreases_as_time_passes(
    policy: EscalationPolicy, minutes: int
) -> None:
    earlier = target_level(timedelta(minutes=minutes), policy)
    later = target_level(timedelta(minutes=minutes + 1), policy)
    assert later >= earlier


@given(
    policies(),
    st.sampled_from(list(EscalationLevel)),
    st.integers(-600, MAX_CLOSE_AFTER_MINUTES + 600),
)
def test_escalation_climbs_at_most_one_level_and_never_goes_back(
    policy: EscalationPolicy, level: EscalationLevel, minutes: int
) -> None:
    decision = plan_escalation(_dose(BASE, level), policy, BASE + timedelta(minutes=minutes))
    if decision.action is EscalationAction.ADVANCE:
        assert decision.to_level == level + 1
    if decision.action is EscalationAction.CLOSE:
        assert minutes >= policy.close_after_minutes


@given(
    policies(),
    st.sampled_from([DoseStatus.TAKEN, DoseStatus.SKIPPED, DoseStatus.UNCONFIRMED]),
    st.integers(-600, 2 * MAX_CLOSE_AFTER_MINUTES),
)
def test_resolved_doses_are_never_escalated(
    policy: EscalationPolicy, status: DoseStatus, minutes: int
) -> None:
    decision = plan_escalation(
        _dose(BASE, EscalationLevel.NONE, status), policy, BASE + timedelta(minutes=minutes)
    )
    assert decision.action is EscalationAction.NONE


@settings(max_examples=60)
@given(policies())
def test_a_dose_left_alone_walks_the_whole_ladder_then_closes(policy: EscalationPolicy) -> None:
    """Simulate the care cycle running every minute with nobody answering."""
    level = EscalationLevel.NONE
    seen = []
    for minute in range(policy.close_after_minutes + 1):
        decision = plan_escalation(_dose(BASE, level), policy, BASE + timedelta(minutes=minute))
        if decision.action is EscalationAction.ADVANCE:
            assert decision.to_level is not None
            level = decision.to_level
            seen.append(level)
        elif decision.action is EscalationAction.CLOSE:
            break
    assert seen == [
        EscalationLevel.ELDER_REMINDED,
        EscalationLevel.CARETAKER_NOTIFIED,
        EscalationLevel.FAMILY_NOTIFIED,
    ]
    assert decision.action is EscalationAction.CLOSE


# --- adherence ------------------------------------------------------------------------------


@given(
    st.lists(
        st.tuples(
            st.sampled_from(list(DoseStatus)),
            st.integers(-3000, 3000),
            st.integers(0, 600),
        ),
        max_size=40,
    ),
    st.integers(0, 240),
)
def test_adherence_counts_partition_due_doses_and_percentages_are_bounded(
    rows: list[tuple[DoseStatus, int, int]], on_time: int
) -> None:
    now = BASE
    doses = []
    for status, offset, answered_after in rows:
        due = now + timedelta(minutes=offset)
        resolved = due + timedelta(minutes=answered_after) if status is DoseStatus.TAKEN else None
        doses.append(
            Dose("d", "c", "m", due, status, EscalationLevel.NONE, resolved, None, None, due)
        )

    summary = summarise_adherence(doses, now, on_time)
    due_count = sum(
        1 for d in doses if d.scheduled_for <= now and d.status is not DoseStatus.CANCELLED
    )

    assert summary.closed_total + summary.awaiting_answer == due_count
    assert 0 <= summary.taken_on_time <= summary.taken
    for percent in (summary.taken_percent, summary.unknown_percent):
        assert percent is None or 0 <= percent <= 100
