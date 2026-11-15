"""Shared helpers for routes where only the elder edits directly.

Everyone else's change is sent as a proposal instead.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData
from starlette.requests import Request

from careloop.domain.models import AppointmentDetails, MedicationDetails, ProposalKind
from careloop.domain.validation import validate_all
from careloop.services.container import Services
from careloop.web.dependencies import Viewer
from careloop.web.forms import advice_from_form
from careloop.web.rendering import flash, local_datetime


def local_today(services: Services, viewer: Viewer) -> date:
    """Today's date in the circle's timezone."""
    return date.fromisoformat(
        local_datetime(services.ctx.clock.now(), viewer.circle.timezone, "%Y-%m-%d")
    )


def submit_proposal(
    services: Services,
    viewer: Viewer,
    form: FormData,
    kind: ProposalKind,
    *,
    details_check: Callable[[], MedicationDetails | AppointmentDetails] | None = None,
    target_id: str | None = None,
) -> None:
    """Validate a suggestion and store it as a proposal.

    The proposed details and "who advised this" are validated together, so every
    error is shown at once.
    """
    checks: list[Callable[[], object]] = [
        lambda: advice_from_form(form, local_today(services, viewer))
    ]
    if details_check is not None:
        checks.append(details_check)
    advice, *details = validate_all(*checks)
    proposed = details[0] if details else None
    services.proposals.propose(
        viewer.member,
        kind=kind,
        target_id=target_id,
        medication_details=proposed if isinstance(proposed, MedicationDetails) else None,
        appointment_details=proposed if isinstance(proposed, AppointmentDetails) else None,
        reason=str(form.get("reason", "")),
        advice=advice,
    )


def proposal_sent(request: Request, viewer: Viewer) -> RedirectResponse:
    """Confirm a suggestion was sent and go to the requests page."""
    decider = viewer.circle.elder_name
    flash(request, f"Sent to {decider} for approval. Nothing changes until it is approved.")
    return RedirectResponse("/proposals", status_code=303)
