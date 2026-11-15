"""Browser accessibility audit with axe-core (optional; not part of pytest).

Starts CareLoop in-process on a temporary database with demo data, opens every
main page as each role in headless Chrome, runs axe-core (WCAG 2.0/2.1/2.2 A
and AA rules) and prints any violations. It then repeats every page at 320 CSS px
wide with the largest text size (WCAG 1.4.10 reflow, no sideways scrolling) and
checks that the first Tab reaches the skip link and that it moves focus to the
main content. Optionally saves screenshots.

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

from datetime import datetime, timezone

import uvicorn
from playwright.sync_api import Page, sync_playwright

from careloop.clock import FixedClock
from careloop.domain.models import Advice, AdviceSource, ProposalKind
from careloop.domain.validation import validate_medication_input
from careloop.notifications.email_sender import UnconfiguredSender
from careloop.services.container import Services, build_services
from careloop.web.app import create_app
from tests.conftest import make_settings

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
    "/report",
]


AXE_RUN = (
    "axe.run(document, {runOnly: {type: 'tag', values: "
    "['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}})"
)
# WCAG 1.4.10 Reflow: content must fit a 320 CSS px wide screen without sideways scrolling.
REFLOW_WIDTH = 320
HAS_SIDEWAYS_SCROLL = "document.documentElement.scrollWidth > document.documentElement.clientWidth"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(services: Services) -> tuple[str, str]:
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
        reason="Take it with breakfast",
        advice=Advice(AdviceSource.DOCTOR, "Dr. Shah"),
    )
    return circle.code, f"/proposals/{proposal.id}"


def sign_in_page(page: Page, base: str, code: str, name: str, passcode: str) -> None:
    page.goto(f"{base}/login")
    page.fill("#field-circle_code", code)
    page.fill("#field-display_name", name)
    page.fill("#field-passcode", passcode)
    page.click('form[action="/login"] button[type=submit]')
    if "/login" in page.url:
        raise RuntimeError(f"sign-in failed for {name}")


def axe_violations(page: Page, axe_source: str, label: str) -> int:
    page.evaluate(axe_source)
    result = page.evaluate(AXE_RUN)
    for violation in result["violations"]:
        targets = [n["target"] for n in violation["nodes"]][:3]
        print(f"[{label}] {page.url}: {violation['id']} ({violation['impact']}) {targets}")
    return len(result["violations"])


def keyboard_problems(page: Page, base: str) -> int:
    """The first Tab lands on the skip link, and following it moves focus to the main content."""
    page.goto(base + "/")
    page.keyboard.press("Tab")
    first = page.evaluate("document.activeElement.className")
    page.keyboard.press("Enter")
    after = page.evaluate("document.activeElement.id")
    problems = 0
    if "skip-link" not in first:
        print(f"[keyboard] first Tab focused {first!r}, expected the skip link")
        problems += 1
    if after != "main":
        print(f"[keyboard] skip link moved focus to {after!r}, expected #main")
        problems += 1
    return problems


def reflow_problems(page: Page, base: str, axe_source: str, role: str, paths: list[str]) -> int:
    """At 320 px wide and the largest text size, no page scrolls sideways or fails axe."""
    page.goto(base + "/")
    page.click('button[name="size"][value="xlarge"]')
    problems = 0
    for path in paths:
        page.goto(base + path)
        if page.evaluate(HAS_SIDEWAYS_SCROLL):
            print(f"[{role} 320px xlarge] {path}: page scrolls sideways")
            problems += 1
        problems += axe_violations(page, axe_source, f"{role} 320px xlarge")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--axe", required=True, help="path to axe.min.js")
    parser.add_argument("--screenshots", help="folder to save screenshots in")
    parser.add_argument(
        "--chrome-channel",
        default="chrome",
        help='installed browser to use; pass "" for the Playwright-bundled Chromium',
    )
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
        browser = pw.chromium.launch(channel=args.chrome_channel or None)
        for role, name, passcode in PEOPLE:
            context = browser.new_context(bypass_csp=True, viewport={"width": 1100, "height": 900})
            page = context.new_page()
            sign_in_page(page, base, code, name, passcode)
            for path in [*PAGES, proposal_page]:
                page.goto(base + path)
                total += axe_violations(page, axe_source, role)
                if shots and path in ("/", proposal_page, "/medications/new"):
                    safe = re.sub(r"[^a-z0-9]+", "-", path.strip("/") or "home")[:20]
                    page.screenshot(path=str(shots / f"{role}-{safe}.png"), full_page=True)
            total += keyboard_problems(page, base)
            context.close()

            narrow = browser.new_context(
                bypass_csp=True, viewport={"width": REFLOW_WIDTH, "height": 800}
            )
            small = narrow.new_page()
            sign_in_page(small, base, code, name, passcode)
            total += reflow_problems(small, base, axe_source, role, [*PAGES, proposal_page])
            narrow.close()
        browser.close()
    server.should_exit = True
    pages_checked = 2 * len(PEOPLE) * (len(PAGES) + 1)
    print(
        f"Accessibility audit: {total} problem(s) across {pages_checked} page views "
        "(axe-core WCAG 2.2 AA, 320 px reflow at the largest text size, keyboard skip link)"
    )
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
