"""Template setup, display filters and flash messages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse

from careloop.demo import DEMO_CIRCLE_CODE, DEMO_PEOPLE
from careloop.domain.models import (
    AdviceSource,
    DoseStatus,
    NotificationStatus,
    ProposalKind,
    ProposalStatus,
    Role,
)
from careloop.domain.permissions import Action, can, can_decide_proposals
from careloop.integrations.google_calendar import add_to_calendar_url
from careloop.notifications.messages import KIND_LABELS
from careloop.security.identifiers import format_circle_code
from careloop.web.routes.preferences import TEXT_SIZE_LABELS, TEXT_SIZES, text_size
from careloop.web.security import csrf_token

TEMPLATE_DIR = Path(__file__).parent / "templates"

ROLE_LABELS = {Role.ELDER: "Elder", Role.CARETAKER: "Caretaker", Role.FAMILY: "Family member"}
DOSE_STATUS_LABELS = {
    DoseStatus.SCHEDULED: "Waiting for an answer",
    DoseStatus.TAKEN: "Taken",
    DoseStatus.SKIPPED: "Skipped",
    DoseStatus.UNCONFIRMED: "No answer recorded",
    DoseStatus.CANCELLED: "Cancelled (schedule changed)",
}
PROPOSAL_KIND_LABELS = {
    ProposalKind.ADD_MEDICATION: "Add a medicine",
    ProposalKind.CHANGE_MEDICATION: "Change a medicine",
    ProposalKind.STOP_MEDICATION: "Stop a medicine",
    ProposalKind.ADD_APPOINTMENT: "Add an appointment",
    ProposalKind.CHANGE_APPOINTMENT: "Change an appointment",
    ProposalKind.CANCEL_APPOINTMENT: "Cancel an appointment",
}
ADVICE_SOURCE_LABELS = {
    AdviceSource.DOCTOR: "A doctor",
    AdviceSource.PHARMACIST: "A pharmacist",
    AdviceSource.CARE_TEAM: "Nobody medical (the helper's own suggestion)",
}
PROPOSAL_STATUS_LABELS = {
    ProposalStatus.PENDING: "Waiting for decision",
    ProposalStatus.APPROVED: "Approved",
    ProposalStatus.REJECTED: "Declined",
    ProposalStatus.CANCELLED: "Withdrawn",
}
NOTIFICATION_STATUS_LABELS = {
    NotificationStatus.PENDING: "Queued",
    NotificationStatus.SENT: "Sent",
    NotificationStatus.FAILED: "Failed - will retry",
    NotificationStatus.GAVE_UP: "Failed - gave up",
    NotificationStatus.NOT_CONFIGURED: "Not sent - email not set up",
    NotificationStatus.NO_EMAIL: "Not sent - no email address",
    NotificationStatus.CANCELLED: "Not needed - already answered",
}


def local_datetime(value: datetime | None, tz_name: str, fmt: str = "%a %d %b %Y, %I:%M %p") -> str:
    """Template filter: format a UTC moment in the circle's timezone."""
    if value is None:
        return ""
    return value.astimezone(ZoneInfo(tz_name)).strftime(fmt).replace(" 0", " ")


def local_time(value: datetime, tz_name: str) -> str:
    """Template filter: format a UTC moment as a local clock time, e.g. 8:00 AM."""
    return value.astimezone(ZoneInfo(tz_name)).strftime("%I:%M %p").lstrip("0")


def demo_mode(request: Request) -> bool:
    """Whether the public demo mode is on (shows the demo banner and panel)."""
    return bool(request.app.state.settings.demo_mode)


def build_templates() -> Jinja2Templates:
    """Create the Jinja environment with CareLoop's filters and globals."""
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))  # autoescape is on for .html
    env = templates.env
    env.filters["local_datetime"] = local_datetime
    env.filters["local_time"] = local_time
    env.filters["circle_code"] = format_circle_code
    env.globals.update(
        csrf_token=csrf_token,
        text_size=text_size,
        demo_mode=demo_mode,
        DEMO_PEOPLE=DEMO_PEOPLE,
        DEMO_CIRCLE_CODE=DEMO_CIRCLE_CODE,
        TEXT_SIZES=TEXT_SIZES,
        TEXT_SIZE_LABELS=TEXT_SIZE_LABELS,
        can=can,
        can_decide_proposals=can_decide_proposals,
        Action=Action,
        Role=Role,
        DoseStatus=DoseStatus,
        ProposalStatus=ProposalStatus,
        ProposalKind=ProposalKind,
        ROLE_LABELS=ROLE_LABELS,
        DOSE_STATUS_LABELS=DOSE_STATUS_LABELS,
        PROPOSAL_KIND_LABELS=PROPOSAL_KIND_LABELS,
        PROPOSAL_STATUS_LABELS=PROPOSAL_STATUS_LABELS,
        ADVICE_SOURCE_LABELS=ADVICE_SOURCE_LABELS,
        NOTIFICATION_STATUS_LABELS=NOTIFICATION_STATUS_LABELS,
        NOTIFICATION_KIND_LABELS=KIND_LABELS,
        calendar_url=add_to_calendar_url,
    )
    return templates


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a message to show on the next page."""
    request.session.setdefault("flash", []).append({"kind": kind, "text": message})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    """Return and clear the messages queued for the next page."""
    messages: list[dict[str, str]] = request.session.pop("flash", [])
    return messages


def render(
    request: Request, template: str, context: dict[str, Any] | None = None, status_code: int = 200
) -> HTMLResponse:
    """Render a template with the queued flash messages."""
    templates: Jinja2Templates = request.app.state.templates
    data = {"flashes": pop_flashes(request), **(context or {})}
    return templates.TemplateResponse(request, template, data, status_code=status_code)
