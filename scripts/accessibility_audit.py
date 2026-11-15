"""Browser accessibility audit with axe-core (optional; not part of pytest).

Starts CareLoop in-process on a temporary database with demo data, opens every
main page as each role in headless Chrome, runs axe-core (WCAG 2.0/2.1/2.2 A
and AA rules) and prints any violations. Optionally saves screenshots.

Requirements (one-off):
    pip install playwright
    npm install axe-core          # gives node_modules/axe-core/axe.min.js
    # plus a local Chrome, or: python -m playwright install chromium

Run:
    python scripts/accessibility_audit.py --axe node_modules/axe-core/axe.min.js \
        [--screenshots out/]
Exit code is 1 if any violation is found.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone  # noqa: E402

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from careloop.clock import FixedClock  # noqa: E402
from careloop.domain.models import ProposalKind  # noqa: E402
from careloop.domain.validation import validate_medication_input  # noqa: E402
from careloop.notifications.email_sender import UnconfiguredSender  # noqa: E402
from careloop.services.container import build_services  # noqa: E402
from careloop.web.app import create_app  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

PEOPLE = [
    ("elder", "Asha Rao", "demo-elder-2468"),
    ("caretaker", "Ravi", "demo-care-1357"),
    ("family", "Meera", "demo-family-8642"),
]
PAGES = [
    "/",
    "/medications",
    "/medications/new",
    "/appointments",
    "/appointments/new",
    "/proposals",
    "/history",
    "/activity",
    "/notifications",
    "/circle",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(services) -> tuple[str, str]:
    now = services.ctx.clock.now()
    today = now.date().isoformat()
    circle, elder = services.circles.create_circle(
        elder_name="Asha Rao",
        passcode=PEOPLE[0][2],
        confirm_passcode=PEOPLE[0][2],
        email="asha@example.com",
        timezone="UTC",
    )
    caretaker = services.circles.add_member(
        elder,
        display_name="Ravi",
        role="caretaker",
        email="ravi@example.com",
        passcode=PEOPLE[1][2],
    )
    services.circles.add_member(
        elder, display_name="Meera", role="family", email="", passcode=PEOPLE[2][2]
    )
    med = services.records.add_medication(
        elder,
        validate_medication_input(
            name="Metformin",
            dosage="1 tablet (500 mg)",
            instructions="With food",
            times=["08:00", "20:00"],
            start_date=today,
            end_date="",
        ),
    )
    proposal = services.proposals.propose(
        caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med,
        medication_details=validate_medication_input(
            name="Metformin",
            dosage="2 tablets",
            instructions="With food",
            times=["09:00"],
            start_date=today,
            end_date="",
        ),
        reason="Doctor's advice",
    )
    return circle.code, f"/proposals/{proposal.id}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--axe", required=True, help="path to axe.min.js")
    parser.add_argument("--screenshots", help="folder to save screenshots in")
    parser.add_argument("--chrome-channel", default="chrome")
    args = parser.parse_args()
    axe_source = Path(args.axe).read_text(encoding="utf-8")

    tmp = Path(tempfile.mkdtemp())
    settings = make_settings(tmp)
    clock = FixedClock(datetime(2026, 11, 15, 7, 55, tzinfo=timezone.utc))
    services = build_services(settings, clock=clock, email_sender=UnconfiguredSender())
    code, proposal_page = seed(services)
    for minutes in (5, 31):  # reach the dose time, then the caretaker step
        clock.advance(minutes=minutes)
        services.cycle.run_once()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, services), port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    shots = Path(args.screenshots) if args.screenshots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    total = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.chrome_channel)
        for role, name, passcode in PEOPLE:
            context = browser.new_context(bypass_csp=True, viewport={"width": 1100, "height": 900})
            page = context.new_page()
            page.goto(f"{base}/login")
            page.fill("#field-circle_code", code)
            page.fill("#field-display_name", name)
            page.fill("#field-passcode", passcode)
            page.click("button[type=submit]")
            for path in [*PAGES, proposal_page]:
                page.goto(base + path)
                page.evaluate(axe_source)
                result = page.evaluate(
                    "axe.run(document, {runOnly: {type: 'tag', values: "
                    "['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}})"
                )
                for violation in result["violations"]:
                    total += 1
                    targets = [n["target"] for n in violation["nodes"]][:3]
                    print(f"[{role}] {path}: {violation['id']} ({violation['impact']}) {targets}")
                if shots and path in ("/", proposal_page, "/medications/new"):
                    safe = re.sub(r"[^a-z0-9]+", "-", path.strip("/") or "home")[:20]
                    page.screenshot(path=str(shots / f"{role}-{safe}.png"), full_page=True)
            context.close()
        browser.close()
    server.should_exit = True
    pages_checked = len(PEOPLE) * (len(PAGES) + 1)
    print(f"axe-core: {total} violation(s) across {pages_checked} page views")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
