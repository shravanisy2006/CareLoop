"""Create the demo care circle so you can try CareLoop straight away.

    python scripts/seed_demo.py

Uses the same settings as the app (.env), so it fills the database the app
uses. Running it again does nothing if the demo circle already exists. The
same demo circle is created automatically on start-up when
CARELOOP_DEMO_MODE=true (used for the public demo link).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from careloop.config import load_settings
from careloop.demo import DEMO_PEOPLE, ensure_demo_circle
from careloop.security.identifiers import format_circle_code
from careloop.services.container import build_services


def main() -> None:
    services = build_services(load_settings())
    circle = ensure_demo_circle(services)
    print(f"Demo care circle ready. Circle code: {format_circle_code(circle.code)}")
    for person in DEMO_PEOPLE:
        print(f"  {person.role.value:<9} name: {person.name:<9} passcode: {person.passcode}")
    print("A Vitamin D3 dose is due about 3 minutes after the circle was first created.")


if __name__ == "__main__":
    main()
