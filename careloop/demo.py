"""Demo care circle for judges and visitors (CARELOOP_DEMO_MODE=true).

Creates, once, a care circle with a fixed code and three known sign-ins, a few
medicines, an appointment, a pending request advised by a doctor, and three
days of sample dose history so the history and doctor summary pages have
something to show. Every page shows a banner saying this is demo data.

Never enable demo mode for real people: the sign-ins are public.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from careloop.domain.models import (
    Advice,
    AdviceSource,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    Member,
    ProposalKind,
    Role,
)
from careloop.domain.schedule import dose_id_for, parse_local_time
from careloop.domain.validation import validate_appointment_input, validate_medication_input
from careloop.services.container import Services

DEMO_CIRCLE_CODE = "DEMOCARE"


@dataclass(frozen=True)
class DemoPerson:
    role: Role
    name: str
    passcode: str
    description: str


DEMO_PEOPLE = (
    DemoPerson(
        Role.ELDER, "Asha Rao", "demo-elder-2468", "The elder: takes medicines and approves changes"
    ),
    DemoPerson(
        Role.CARETAKER, "Ravi", "demo-care-1357", "Caretaker: told first about unconfirmed doses"
    ),
    DemoPerson(
        Role.FAMILY,
        "Meera",
        "demo-family-8642",
        "Family member: told if a dose is still unconfirmed",
    ),
)


def demo_person(role: str) -> DemoPerson | None:
    return next((p for p in DEMO_PEOPLE if p.role.value == role), None)


def _sample_history(
    services: Services,
    circle: Circle,
    elder: Member,
    caretaker: Member,
    medication_id: str,
    times: tuple[str, ...],
    today: date,
) -> None:
    """Three past days of answered and unanswered doses, so the history,
    adherence and doctor summary pages have realistic content."""
    tz = ZoneInfo(circle.timezone)
    pattern = [
        DoseStatus.TAKEN,
        DoseStatus.TAKEN,
        DoseStatus.SKIPPED,
        DoseStatus.TAKEN,
        DoseStatus.UNCONFIRMED,
        DoseStatus.TAKEN,
    ]
    doses = []
    index = 0
    for days_ago in (3, 2, 1):
        day = today - timedelta(days=days_ago)
        for value in times:
            scheduled = datetime.combine(day, parse_local_time(value), tz)
            status = pattern[index % len(pattern)]
            by = caretaker if index % 4 == 3 else elder
            answered = status in (DoseStatus.TAKEN, DoseStatus.SKIPPED)
            doses.append(
                Dose(
                    id=dose_id_for(medication_id, 0, scheduled),
                    circle_id=circle.id,
                    medication_id=medication_id,
                    scheduled_for=scheduled.astimezone(ZoneInfo("UTC")),
                    status=status,
                    escalation_level=EscalationLevel.ELDER_REMINDED
                    if answered
                    else EscalationLevel.FAMILY_NOTIFIED,
                    resolved_at=scheduled + timedelta(minutes=10 + 7 * index) if answered else None,
                    resolved_by=by.id if answered else None,
                    resolved_by_role=by.role if answered else None,
                    created_at=scheduled,
                )
            )
            index += 1
    services.ctx.repo.insert_doses_if_absent(doses)


def ensure_demo_circle(services: Services) -> Circle:
    """Create the demo care circle if it does not exist yet (idempotent)."""
    repo = services.ctx.repo
    existing = repo.get_circle_by_code(DEMO_CIRCLE_CODE)
    if existing is not None:
        return existing

    tz = services.ctx.settings.default_timezone
    now = services.ctx.clock.now()
    local_now = now.astimezone(ZoneInfo(tz))
    today = local_now.date()
    elder_info, caretaker_info, family_info = DEMO_PEOPLE

    circle, elder = services.circles.create_circle(
        elder_name=elder_info.name,
        passcode=elder_info.passcode,
        confirm_passcode=elder_info.passcode,
        email="",
        timezone=tz,
        circle_code=DEMO_CIRCLE_CODE,
    )
    caretaker = services.circles.add_member(
        elder,
        display_name=caretaker_info.name,
        role="caretaker",
        email="",
        passcode=caretaker_info.passcode,
    )
    services.circles.add_member(
        elder,
        display_name=family_info.name,
        role="family",
        email="",
        passcode=family_info.passcode,
    )

    # A dose a few minutes after start-up, so visitors can watch it become due.
    soon = (local_now + timedelta(minutes=3)).strftime("%H:%M")
    metformin_times = ("08:00", "20:00")
    metformin = services.records.add_medication(
        elder,
        validate_medication_input(
            name="Metformin",
            dosage="1 tablet (500 mg)",
            instructions="With food",
            times=list(metformin_times),
            start_date=(today - timedelta(days=30)).isoformat(),
            end_date="",
        ),
    )
    services.records.add_medication(
        elder,
        validate_medication_input(
            name="Vitamin D3",
            dosage="1 capsule",
            instructions="After lunch",
            times=[soon],
            start_date=today.isoformat(),
            end_date="",
        ),
    )
    amlodipine = services.records.add_medication(
        elder,
        validate_medication_input(
            name="Amlodipine",
            dosage="1 tablet (5 mg)",
            instructions="In the morning",
            times=["09:00"],
            start_date=(today - timedelta(days=30)).isoformat(),
            end_date="",
        ),
    )
    services.records.add_appointment(
        elder,
        validate_appointment_input(
            doctor_name="Dr. Shah",
            purpose="Diabetes check-up",
            location="City Clinic, Pune",
            date_value=(today + timedelta(days=3)).isoformat(),
            time_value="10:30",
            notes="",
            tz_name=tz,
            now=now,
        ),
    )
    services.proposals.propose(
        caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=amlodipine,
        medication_details=validate_medication_input(
            name="Amlodipine",
            dosage="1 tablet (5 mg)",
            instructions="After breakfast",
            times=["10:00"],
            start_date=(today - timedelta(days=30)).isoformat(),
            end_date="",
        ),
        reason="Taking it after breakfast should stop the morning dizziness.",
        advice=Advice(AdviceSource.DOCTOR, "Dr. Shah", today - timedelta(days=1)),
    )
    _sample_history(services, circle, elder, caretaker, metformin, metformin_times, today)
    return circle
