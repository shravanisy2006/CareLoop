"""Doctor appointments, with the same rule as medicines.

The elder changes them directly; anyone else's change is a proposal until it is approved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import Appointment, AppointmentStatus, ProposalKind
from careloop.domain.permissions import Action, can
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.forms import appointment_from_form, appointment_to_values, form_values
from careloop.web.proposing import proposal_sent, submit_proposal
from careloop.web.rendering import flash, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/appointments")


def _form_page(
    request: Request,
    viewer: Viewer,
    *,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    appointment: Appointment | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        request,
        "appointments/form.html",
        {"viewer": viewer, "values": values, "errors": errors or {}, "appointment": appointment},
        status_code=status_code,
    )


def _cancel_page(
    request: Request,
    viewer: Viewer,
    appointment: Appointment,
    *,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        request,
        "appointments/cancel.html",
        {
            "viewer": viewer,
            "appointment": appointment,
            "values": values or {"advice_source": "doctor"},
            "errors": errors or {},
        },
        status_code=status_code,
    )


@router.get("")
def list_appointments(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show upcoming appointments, then past and cancelled ones."""
    now = services.ctx.clock.now()
    appointments = services.records.list_appointments(viewer.member)
    upcoming = [
        a
        for a in appointments
        if a.status is AppointmentStatus.SCHEDULED and a.details.starts_at > now
    ]
    past = [a for a in appointments if a not in upcoming]
    return render(
        request,
        "appointments/list.html",
        {"viewer": viewer, "upcoming": upcoming, "past": list(reversed(past))},
    )


@router.get("/new")
def new_appointment(request: Request, viewer: Viewer = Depends(get_viewer)) -> Response:
    """Show the form to add (or suggest) an appointment."""
    return _form_page(request, viewer, values={"advice_source": "doctor"})


@router.post("")
async def create_appointment(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Add an appointment (elder) or send it as a suggestion (helpers)."""
    form = await verified_form(request)
    tz, now = viewer.circle.timezone, services.ctx.clock.now()
    try:
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            details = appointment_from_form(form, tz, now)
            services.records.add_appointment(viewer.member, details)
            flash(request, f"Appointment with {details.doctor_name} was added.")
            return RedirectResponse("/appointments", status_code=303)
        submit_proposal(
            services,
            viewer,
            form,
            ProposalKind.ADD_APPOINTMENT,
            details_check=lambda: appointment_from_form(form, tz, now),
        )
    except ValidationFailed as exc:
        return _form_page(
            request, viewer, values=form_values(form), errors=exc.field_errors, status_code=422
        )
    return proposal_sent(request, viewer)


@router.get("/{appointment_id}/edit")
def edit_appointment_page(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show the form to change (or suggest changing) an appointment."""
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    values = {
        **appointment_to_values(appointment, viewer.circle.timezone),
        "advice_source": "doctor",
    }
    return _form_page(request, viewer, values=values, appointment=appointment)


@router.post("/{appointment_id}/edit")
async def edit_appointment(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Change an appointment (elder) or send the change as a suggestion."""
    form = await verified_form(request)
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    tz, now = viewer.circle.timezone, services.ctx.clock.now()
    try:
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            details = appointment_from_form(form, tz, now)
            services.records.change_appointment(viewer.member, appointment.id, details)
            flash(request, "The appointment was updated.")
            return RedirectResponse("/appointments", status_code=303)
        submit_proposal(
            services,
            viewer,
            form,
            ProposalKind.CHANGE_APPOINTMENT,
            details_check=lambda: appointment_from_form(form, tz, now),
            target_id=appointment.id,
        )
    except ValidationFailed as exc:
        return _form_page(
            request,
            viewer,
            values=form_values(form),
            errors=exc.field_errors,
            appointment=appointment,
            status_code=422,
        )
    return proposal_sent(request, viewer)


@router.get("/{appointment_id}/cancel")
def cancel_appointment_page(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Ask for confirmation before cancelling (or suggesting to cancel)."""
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    return _cancel_page(request, viewer, appointment)


@router.post("/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Cancel an appointment (elder) or suggest cancelling it."""
    form = await verified_form(request)
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
        services.records.cancel_appointment(viewer.member, appointment.id)
        flash(request, "The appointment was cancelled.")
        return RedirectResponse("/appointments", status_code=303)
    try:
        submit_proposal(
            services, viewer, form, ProposalKind.CANCEL_APPOINTMENT, target_id=appointment.id
        )
    except ValidationFailed as exc:
        return _cancel_page(
            request,
            viewer,
            appointment,
            values=form_values(form),
            errors=exc.field_errors,
            status_code=422,
        )
    return proposal_sent(request, viewer)
