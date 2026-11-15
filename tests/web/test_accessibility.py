"""Automated accessibility checks on every rendered page, plus colour contrast.

These are static checks of the real HTML the server returns. They cover
structure a screen reader depends on (language, title, one h1, heading order,
labels, button names, landmarks) and WCAG contrast for the colour pairs the
stylesheet uses. They do not replace testing with a real screen reader; see
TESTING.md for the separate axe-core browser audit.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from careloop.domain.models import ProposalKind
from careloop.domain.validation import validate_appointment_input
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, FAMILY_PASSCODE, medication_details
from tests.web.helpers import CSRF_PATTERN, make_client, sign_in

CSS_PATH = Path(__file__).resolve().parents[2] / "careloop" / "web" / "static" / "careloop.css"


class PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lang = None
        self.title = ""
        self._in_title = False
        self.headings: list[int] = []
        self.label_for: set[str] = set()
        self.controls: list[dict[str, str]] = []
        self.buttons: list[str] = []
        self._button_text: list[str] | None = None
        self.links: list[tuple[str, str]] = []
        self._link: list | None = None
        self.navs_without_label = 0
        self.main_ids: list[str] = []
        self.table_headers_without_scope = 0
        self._label_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "label":
            self._label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag in ("input", "select", "textarea"):
            if a.get("type") not in ("hidden", "submit"):
                self.controls.append({**a, "_wrapped": str(self._label_depth > 0), "_tag": tag})
        elif tag == "button":
            self._button_text = []
        elif tag == "a":
            self._link = [a.get("href", ""), []]
        elif tag == "nav" and not (a.get("aria-label") or a.get("aria-labelledby")):
            self.navs_without_label += 1
        elif tag == "main":
            self.main_ids.append(a.get("id", ""))
        elif tag == "th" and "scope" not in a:
            self.table_headers_without_scope += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "label":
            self._label_depth -= 1
        elif tag == "button" and self._button_text is not None:
            self.buttons.append(" ".join("".join(self._button_text).split()))
            self._button_text = None
        elif tag == "a" and self._link is not None:
            self.links.append((self._link[0], " ".join("".join(self._link[1]).split())))
            self._link = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._button_text is not None:
            self._button_text.append(data)
        if self._link is not None:
            self._link[1].append(data)


def audit(html: str) -> PageAudit:
    parser = PageAudit()
    parser.feed(html)
    return parser


def assert_accessible(html: str, page: str) -> None:
    result = audit(html)
    assert result.lang == "en", page
    assert result.title.strip(), f"{page}: empty <title>"
    assert result.headings.count(1) == 1, f"{page}: needs exactly one h1, got {result.headings}"
    for previous, current in zip(result.headings, result.headings[1:], strict=False):
        assert current <= previous + 1, f"{page}: heading jumps from h{previous} to h{current}"
    for control in result.controls:
        labelled = (
            control.get("id") in result.label_for
            or control["_wrapped"] == "True"
            or control.get("aria-label")
            or control.get("aria-labelledby")
        )
        assert labelled, f"{page}: unlabelled {control['_tag']} {control.get('name')}"
    assert all(result.buttons), f"{page}: a button has no text"
    assert all(text for href, text in result.links), f"{page}: a link has no text"
    assert result.links and result.links[0] == ("#main", "Skip to main content"), page
    assert result.main_ids == ["main"], page
    assert result.navs_without_label == 0, f"{page}: <nav> without a label"
    assert result.table_headers_without_scope == 0, f"{page}: <th> without scope"
    assert "outline: none" not in html


def _prepare(services, circle, clock):
    med_id = services.records.add_medication(circle.elder, medication_details())
    proposal = services.proposals.propose(
        circle.caretaker,
        kind=ProposalKind.CHANGE_MEDICATION,
        target_id=med_id,
        medication_details=medication_details(times="09:00"),
        reason="Doctor said so",
    )
    appointment_id = services.records.add_appointment(
        circle.elder,
        validate_appointment_input(
            doctor_name="Dr. Shah",
            purpose="Check-up",
            location="",
            date_value="2026-11-20",
            time_value="10:30",
            notes="",
            tz_name="Asia/Kolkata",
            now=clock.now(),
        ),
    )
    clock.advance(minutes=45)
    services.cycle.run_once()
    return med_id, proposal.id, appointment_id


PUBLIC_PAGES = ["/", "/login", "/start"]


@pytest.mark.parametrize("page", PUBLIC_PAGES)
def test_public_pages_are_accessible(services, page):
    response = make_client(services).get(page)
    assert_accessible(response.text, page)


@pytest.mark.parametrize(
    ("name", "passcode"),
    [("Asha Rao", ELDER_PASSCODE), ("Ravi", CARETAKER_PASSCODE), ("Meera", FAMILY_PASSCODE)],
)
def test_signed_in_pages_are_accessible_for_each_role(services, circle, clock, name, passcode):
    med_id, proposal_id, appointment_id = _prepare(services, circle, clock)
    client = make_client(services)
    sign_in(client, circle.code, name, passcode)
    pages = [
        "/",
        "/medications",
        "/medications/new",
        f"/medications/{med_id}",
        f"/medications/{med_id}/edit",
        "/appointments",
        "/appointments/new",
        "/proposals",
        f"/proposals/{proposal_id}",
        "/history",
        "/activity",
        "/notifications",
        "/circle",
        f"/appointments/{appointment_id}/edit",
        f"/appointments/{appointment_id}/cancel",
        "/report",
    ]
    for page in pages:
        response = client.get(page)
        assert response.status_code == 200, page
        assert_accessible(response.text, f"{name} {page}")


def test_form_errors_are_announced_and_linked_to_fields(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    token = CSRF_PATTERN.search(client.get("/medications/new").text).group(1)
    page = client.post(
        "/medications",
        data={"csrf_token": token, "name": "", "dosage": "", "start_date": "2026-11-15"},
    ).text
    assert 'role="alert"' in page
    assert 'href="#field-name"' in page and 'id="field-name"' in page
    assert 'aria-describedby="error-name"' in page and 'aria-invalid="true"' in page


def test_success_messages_use_a_live_region(services, circle):
    client = make_client(services)
    sign_in(client, circle.code, "Asha Rao", ELDER_PASSCODE)
    page = client.get("/").text
    assert 'role="status" aria-live="polite"' in page
    assert "Welcome, Asha Rao." in page


# --- Colour contrast (WCAG 2.2 AA: 4.5:1 for normal text) ---------------------


def _css_variables() -> dict[str, str]:
    root = re.search(r":root\s*{([^}]*)}", CSS_PATH.read_text()).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


TEXT_PAIRS = [
    ("color-text", "color-background"),
    ("color-text", "color-surface"),
    ("color-muted", "color-background"),
    ("color-muted", "color-surface"),
    ("color-link", "color-background"),
    ("color-link", "color-surface"),
    ("color-primary-text", "color-primary"),
    ("color-primary", "color-background"),
    ("color-danger-text", "color-danger"),
    ("color-success-text", "color-success-bg"),
    ("color-warning-text", "color-warning-bg"),
    ("color-danger-on-bg", "color-danger-bg"),
    ("color-danger-on-bg", "color-background"),
]


@pytest.mark.parametrize(("foreground", "background"), TEXT_PAIRS)
def test_text_colour_pairs_meet_wcag_aa(foreground, background):
    colours = _css_variables()
    ratio = contrast(colours[foreground], colours[background])
    assert ratio >= 4.5, f"{foreground} on {background} is {ratio:.2f}:1"


def test_controls_are_large_enough_to_tap():
    css = CSS_PATH.read_text()
    sizes = [int(px) for px in re.findall(r"min-height:\s*(\d+)px", css)]
    assert sizes and min(sizes) >= 48  # WCAG 2.5.5 target size (enhanced) is 44px
    assert "--font-size-base: 1.25rem" in css  # 20px body text


def test_every_table_is_in_a_named_keyboard_scrollable_region():
    """Wide tables must scroll inside their own region, never make the page scroll (WCAG 1.4.10)."""
    templates = Path(__file__).resolve().parents[2] / "careloop" / "web" / "templates"
    wrapper = re.compile(
        r'<div class="table-scroll" role="region" aria-label="[^"]+" tabindex="0">\s*<table'
    )
    for template in templates.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert text.count("<table") == len(wrapper.findall(text)), template.name
