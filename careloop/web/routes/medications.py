"""Medicines.

The elder changes them directly; anyone else's change becomes a proposal that changes nothing until
it is approved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import ValidationFailed
from careloop.domain.models import Medication, ProposalKind, ProposalStatus
from careloop.domain.permissions import Action, can
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.forms import form_values, medication_from_form, medication_to_values
from careloop.web.proposing import local_today, proposal_sent, submit_proposal
from careloop.web.rendering import flash, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/medications")


def _form_page(
    request: Request,
    viewer: Viewer,
    *,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    medication: Medication | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        request,
        "medications/form.html",
        {"viewer": viewer, "values": values, "errors": errors or {}, "medication": medication},
        status_code=status_code,
    )


@router.get("")
def list_medications(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show all medicines in the circle."""
    medications = services.records.list_medications(viewer.member)
    return render(request, "medications/list.html", {"viewer": viewer, "medications": medications})


@router.get("/new")
def new_medication(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show the form to add (or suggest) a medicine."""
    today = local_today(services, viewer).isoformat()
    return _form_page(request, viewer, values={"start_date": today, "advice_source": "doctor"})


@router.post("")
async def create_medication(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Add a medicine (elder) or send it as a suggestion (helpers)."""
    form = await verified_form(request)
    try:
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            details = medication_from_form(form)
            medication_id = services.records.add_medication(viewer.member, details)
            flash(request, f"{details.name} was added.")
            return RedirectResponse(f"/medications/{medication_id}", status_code=303)
        submit_proposal(
            services,
            viewer,
            form,
            ProposalKind.ADD_MEDICATION,
            details_check=lambda: medication_from_form(form),
        )
    except ValidationFailed as exc:
        return _form_page(
            request, viewer, values=form_values(form), errors=exc.field_errors, status_code=422
        )
    return proposal_sent(request, viewer)


@router.get("/{medication_id}")
def medication_detail(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show one medicine and any pending suggestions about it."""
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
            "values": {},
            "errors": {},
        },
    )


@router.get("/{medication_id}/edit")
def edit_medication_page(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show the form to change (or suggest changing) a medicine."""
    medication = services.records.get_medication(viewer.member, medication_id)
    values = {**medication_to_values(medication), "advice_source": "doctor"}
    return _form_page(request, viewer, values=values, medication=medication)


@router.post("/{medication_id}/edit")
async def edit_medication(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Change a medicine (elder) or send the change as a suggestion."""
    form = await verified_form(request)
    medication = services.records.get_medication(viewer.member, medication_id)
    try:
        if can(viewer.member, Action.EDIT_RECORDS_DIRECTLY):
            details = medication_from_form(form)
            services.records.change_medication(viewer.member, medication.id, details)
            flash(request, f"{details.name} was updated. Upcoming doses follow the new schedule.")
            return RedirectResponse(f"/medications/{medication.id}", status_code=303)
        submit_proposal(
            services,
            viewer,
            form,
            ProposalKind.CHANGE_MEDICATION,
            details_check=lambda: medication_from_form(form),
            target_id=medication.id,
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
    return proposal_sent(request, viewer)


@router.post("/{medication_id}/stop")
async def stop_medication(
    medication_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Stop a medicine (elder) or suggest stopping it."""
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
        submit_proposal(
            services, viewer, form, ProposalKind.STOP_MEDICATION, target_id=medication.id
        )
    except ValidationFailed as exc:
        return render(
            request,
            "medications/detail.html",
            {
                "viewer": viewer,
                "medication": medication,
                "pending": [],
                "values": form_values(form),
                "errors": exc.field_errors,
            },
            status_code=422,
        )
    return proposal_sent(request, viewer)
