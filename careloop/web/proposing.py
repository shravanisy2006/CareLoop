"""Shared helpers for routes where the elder edits directly and everyone else
sends a proposal instead."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData
from starlette.requests import Request

from careloop.domain.models import (
    APPOINTMENT_PROPOSAL_KINDS,
    MEDICATION_PROPOSAL_KINDS,
    ProposalKind,
)
from careloop.services.container import Services
from careloop.web.dependencies import Viewer
from careloop.web.forms import advice_from_form, collect_errors
from careloop.web.rendering import flash, local_datetime


def local_today(services: Services, viewer: Viewer) -> date:
    return date.fromisoformat(
        local_datetime(services.ctx.clock.now(), viewer.circle.timezone, "%Y-%m-%d")
    )


def submit_proposal(
    services: Services,
    viewer: Viewer,
    form: FormData,
    kind: ProposalKind,
    *,
    details_check: Any = None,
    target_id: str | None = None,
) -> None:
    """Validate the proposed details and "who advised this" together (so every
    error is shown at once), then store the proposal."""
    checks = [lambda: advice_from_form(form, local_today(services, viewer))]
    if details_check is not None:
        checks.append(details_check)
    advice, *details = collect_errors(*checks)
    proposed = details[0] if details else None
    services.proposals.propose(
        viewer.member,
        kind=kind,
        target_id=target_id,
        medication_details=proposed if kind in MEDICATION_PROPOSAL_KINDS else None,
        appointment_details=proposed if kind in APPOINTMENT_PROPOSAL_KINDS else None,
        reason=str(form.get("reason", "")),
        advice=advice,
    )


def proposal_sent(request: Request, viewer: Viewer) -> RedirectResponse:
    decider = viewer.circle.elder_name
    flash(request, f"Sent to {decider} for approval. Nothing changes until it is approved.")
    return RedirectResponse("/proposals", status_code=303)
