"""Medicines. The elder changes them directly; anyone else's change becomes a
proposal for the elder to approve."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import ProposalKind, ProposalStatus
from careloop.domain.permissions import Action, can
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.forms import form_values, medication_from_form, medication_to_values
from careloop.web.rendering import flash, local_datetime, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/medications")


def _form_page(request, viewer, *, values, errors=None, medication=None, status_code=200):
    return render(
        request,
        "medications/form.html",
        {"viewer": viewer, "values": values, "errors": errors or {}, "medication": medication},
        status_code=status_code,
    )


def _proposal_sent(request: Request, viewer: Viewer) -> RedirectResponse:
    flash(
        request,
        f"Sent to {viewer.circle.elder_name} for approval. Nothing changes until they agree.",
    )
    return RedirectResponse("/proposals", status_code=303)


@router.get("")
def list_medications(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    return render(
        request,
        "medications/list.html",
        {
            "viewer": viewer,
            "medications": services.records.list_medications(viewer.member),
        },
    )


@router.get("/new")
def new_medication(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    today = local_datetime(services.ctx.clock.now(), viewer.circle.timezone, "%Y-%m-%d")
    return _form_page(request, viewer, values={"start_date": today})


@router.post("")
async def create_medication(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    try:
        details = medication_from_form(form)
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            medication_id = services.records.add_medication(viewer.member, details)
            flash(request, f"{details.name} was added.")
            return RedirectResponse(f"/medications/{medication_id}", status_code=303)
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.ADD_MEDICATION,
            medication_details=details,
            reason=str(form.get("reason", "")),
        )
    except ValidationFailed as exc:
        return _form_page(
            request, viewer, values=form_values(form), errors=exc.field_errors, status_code=422
        )
    return _proposal_sent(request, viewer)


@router.get("/{medication_id}")
def medication_detail(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    medication = services.records.get_medication(viewer.member, medication_id)
    pending = [
        p
        for p in services.proposals.list(viewer.member, ProposalStatus.PENDING)
        if p.target_id == medication.id
    ]
    return render(
        request,
        "medications/detail.html",
        {
            "viewer": viewer,
            "medication": medication,
            "pending": pending,
        },
    )


@router.get("/{medication_id}/edit")
def edit_medication_page(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    medication = services.records.get_medication(viewer.member, medication_id)
    return _form_page(
        request, viewer, values=medication_to_values(medication), medication=medication
    )


@router.post("/{medication_id}/edit")
async def edit_medication(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    medication = services.records.get_medication(viewer.member, medication_id)
    try:
        details = medication_from_form(form)
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            services.records.change_medication(viewer.member, medication.id, details)
            flash(request, f"{details.name} was updated. Upcoming doses follow the new schedule.")
            return RedirectResponse(f"/medications/{medication.id}", status_code=303)
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.CHANGE_MEDICATION,
            target_id=medication.id,
            medication_details=details,
            reason=str(form.get("reason", "")),
        )
    except ValidationFailed as exc:
        return _form_page(
            request,
            viewer,
            values=form_values(form),
            errors=exc.field_errors,
            medication=medication,
            status_code=422,
        )
    return _proposal_sent(request, viewer)


@router.post("/{medication_id}/stop")
async def stop_medication(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    medication = services.records.get_medication(viewer.member, medication_id)
    if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
        services.records.stop_medication(viewer.member, medication.id)
        flash(
            request,
            f"{medication.details.name} was stopped. No more reminders will be sent for it.",
        )
        return RedirectResponse(f"/medications/{medication.id}", status_code=303)
    try:
        services.proposals.propose(
            viewer.member,
            kind=ProposalKind.STOP_MEDICATION,
            target_id=medication.id,
            reason=str(form.get("reason", "")),
        )
    except ValidationFailed as exc:
        flash(request, next(iter(exc.field_errors.values())), "error")
        return RedirectResponse(f"/medications/{medication.id}", status_code=303)
    return _proposal_sent(request, viewer)
