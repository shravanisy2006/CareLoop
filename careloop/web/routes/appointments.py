"""Doctor appointments, with the same elder-decides rule as medicines."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import AppointmentStatus, ProposalKind
from careloop.domain.permissions import Action, can
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.forms import appointment_from_form, appointment_to_values, form_values
from careloop.web.rendering import flash, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/appointments")


def _form_page(request, viewer, *, values, errors=None, appointment=None, status_code=200):
    return render(
        request,
        "appointments/form.html",
        {"viewer": viewer, "values": values, "errors": errors or {}, "appointment": appointment},
        status_code=status_code,
    )


def _proposal_sent(request: Request, viewer: Viewer) -> RedirectResponse:
    flash(
        request,
        f"Sent to {viewer.circle.elder_name} for approval. Nothing changes until they agree.",
    )
    return RedirectResponse("/proposals", status_code=303)


@router.get("")
def list_appointments(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
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
        {
            "viewer": viewer,
            "upcoming": upcoming,
            "past": list(reversed(past)),
        },
    )


@router.get("/new")
def new_appointment(request: Request, viewer: Viewer = Depends(get_viewer)):
    return _form_page(request, viewer, values={})


@router.post("")
async def create_appointment(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    try:
        details = appointment_from_form(form, viewer.circle.timezone, services.ctx.clock.now())
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            services.records.add_appointment(viewer.member, details)
            flash(request, f"Appointment with {details.doctor_name} was added.")
            return RedirectResponse("/appointments", status_code=303)
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.ADD_APPOINTMENT,
            appointment_details=details,
            reason=str(form.get("reason", "")),
        )
    except ValidationFailed as exc:
        return _form_page(
            request, viewer, values=form_values(form), errors=exc.field_errors, status_code=422
        )
    return _proposal_sent(request, viewer)


@router.get("/{appointment_id}/edit")
def edit_appointment_page(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    return _form_page(
        request,
        viewer,
        values=appointment_to_values(appointment, viewer.circle.timezone),
        appointment=appointment,
    )


@router.post("/{appointment_id}/edit")
async def edit_appointment(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    try:
        details = appointment_from_form(form, viewer.circle.timezone, services.ctx.clock.now())
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            services.records.change_appointment(viewer.member, appointment.id, details)
            flash(request, "The appointment was updated.")
            return RedirectResponse("/appointments", status_code=303)
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.CHANGE_APPOINTMENT,
            target_id=appointment.id,
            appointment_details=details,
            reason=str(form.get("reason", "")),
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
    return _proposal_sent(request, viewer)


@router.post("/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    appointment = services.records.get_appointment(viewer.member, appointment_id)
    if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
        services.records.cancel_appointment(viewer.member, appointment.id)
        flash(request, "The appointment was cancelled.")
        return RedirectResponse("/appointments", status_code=303)
    try:
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.CANCEL_APPOINTMENT,
            target_id=appointment.id,
            reason=str(form.get("reason", "")),
        )
    except ValidationFailed as exc:
        flash(request, next(iter(exc.field_errors.values())), "error")
        return RedirectResponse("/appointments", status_code=303)
    return _proposal_sent(request, viewer)
