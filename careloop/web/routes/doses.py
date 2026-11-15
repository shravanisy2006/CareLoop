"""Recording that a dose was taken or skipped."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import AlreadyResolved, InvalidStateTransition
from careloop.domain.models import DoseStatus
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.rendering import flash, local_time
from careloop.web.security import safe_redirect_target, verified_form

router = APIRouter()


@router.post("/doses/{dose_id}/record")
async def record_dose(
    dose_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Record a dose as taken or skipped, then return to the page it came from."""
    form = await verified_form(request)
    try:
        dose = services.doses.record(viewer.member, dose_id, str(form.get("outcome", "")))
    except (AlreadyResolved, InvalidStateTransition) as exc:
        flash(request, exc.message, "error")
    else:
        word = "taken" if dose.status is DoseStatus.TAKEN else "skipped"
        recorded_at = dose.resolved_at or services.ctx.clock.now()
        when = local_time(recorded_at, viewer.circle.timezone)
        flash(request, f"Thank you. Recorded as {word} at {when}.")
    return RedirectResponse(safe_redirect_target(form.get("next")), status_code=303)
