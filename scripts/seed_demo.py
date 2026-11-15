"""Create a demo care circle so you can try CareLoop straight away.

    python scripts/seed_demo.py

Uses the same settings as the app (.env), so run it against the database the
app uses. It prints the circle code and the three demo sign-ins. The demo
passcodes are printed because they are throwaway test data; never use them
for real people.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from careloop.config import load_settings  # noqa: E402
from careloop.domain.models import Advice, AdviceSource, ProposalKind  # noqa: E402
from careloop.domain.validation import (  # noqa: E402
    validate_appointment_input,
    validate_medication_input,
)
from careloop.security.identifiers import format_circle_code  # noqa: E402
from careloop.services.container import build_services  # noqa: E402

DEMO_PEOPLE = {
    "elder": ("Asha Rao", "demo-elder-2468"),
    "caretaker": ("Ravi", "demo-care-1357"),
    "family": ("Meera", "demo-family-8642"),
}


def main() -> None:
    settings = load_settings()
    services = build_services(settings)
    tz = settings.default_timezone
    now = services.ctx.clock.now()
    today = now.astimezone(ZoneInfo(tz)).date()
    local_now = now.astimezone(ZoneInfo(tz))

    elder_name, elder_pass = DEMO_PEOPLE["elder"]
    circle, elder = services.circles.create_circle(
        elder_name=elder_name,
        passcode=elder_pass,
        confirm_passcode=elder_pass,
        email="",
        timezone=tz,
    )
    caretaker = services.circles.add_member(
        elder,
        display_name=DEMO_PEOPLE["caretaker"][0],
        role="caretaker",
        email="",
        passcode=DEMO_PEOPLE["caretaker"][1],
    )
    services.circles.add_member(
        elder,
        display_name=DEMO_PEOPLE["family"][0],
        role="family",
        email="",
        passcode=DEMO_PEOPLE["family"][1],
    )

    # One dose a few minutes from now, so the escalation can be watched live.
    soon = (local_now + timedelta(minutes=3)).strftime("%H:%M")
    services.records.add_medication(
        elder,
        validate_medication_input(
            name="Metformin",
            dosage="1 tablet (500 mg)",
            instructions="With food",
            times=[soon, "21:00"],
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
            start_date=today.isoformat(),
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
            start_date=today.isoformat(),
            end_date="",
        ),
        reason="Taking it after breakfast should stop the dizziness.",
        advice=Advice(AdviceSource.DOCTOR, "Dr. Shah", today - timedelta(days=1)),
    )

    print(f"Demo care circle created. Circle code: {format_circle_code(circle.code)}")
    for role, (name, passcode) in DEMO_PEOPLE.items():
        print(f"  {role:<9} name: {name:<9} passcode: {passcode}")
    print(f"A Metformin dose is due at {soon} ({tz}). Leave the app running to see the escalation.")


if __name__ == "__main__":
    main()
