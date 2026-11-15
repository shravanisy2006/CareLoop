"""Reviewing suggested changes. Only the elder can approve or decline."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import ConcurrencyConflict, InvalidStateTransition
from careloop.domain.models import ProposalStatus
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer, member_names
from careloop.web.rendering import flash, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/proposals")


@router.get("")
def list_proposals(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show pending requests with plain summaries, then decided ones."""
    proposals = services.proposals.list(viewer.member)
    pending = [p for p in proposals if p.status is ProposalStatus.PENDING]
    return render(
        request,
        "proposals/list.html",
        {
            "viewer": viewer,
            "pending": pending,
            "summaries": {p.id: services.proposals.summary(viewer.member, p) for p in pending},
            "decided": [p for p in proposals if p.status is not ProposalStatus.PENDING][:30],
            "names": member_names(services, viewer),
        },
    )


@router.get("/{proposal_id}")
def proposal_detail(
    proposal_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Show one request: summary, who advised it, and current vs suggested."""
    proposal = services.proposals.get(viewer.member, proposal_id)
    return render(
        request,
        "proposals/detail.html",
        {
            "viewer": viewer,
            "proposal": proposal,
            "current": services.proposals.current_target(viewer.member, proposal),
            "summary": services.proposals.summary(viewer.member, proposal),
            "names": member_names(services, viewer),
        },
    )


@router.post("/{proposal_id}/approve")
async def approve(
    proposal_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Approve a request (elder or trusted decision-maker)."""
    form = await verified_form(request)
    try:
        services.proposals.approve(viewer.member, proposal_id, str(form.get("note", "")))
    except (ConcurrencyConflict, InvalidStateTransition) as exc:
        flash(request, exc.message, "error")
        return RedirectResponse(f"/proposals/{proposal_id}", status_code=303)
    flash(request, "Approved. The change is now in place.")
    return RedirectResponse("/proposals", status_code=303)


@router.post("/{proposal_id}/reject")
async def reject(
    proposal_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Decline a request (elder or trusted decision-maker)."""
    form = await verified_form(request)
    try:
        services.proposals.reject(viewer.member, proposal_id, str(form.get("note", "")))
    except (ConcurrencyConflict, InvalidStateTransition) as exc:
        flash(request, exc.message, "error")
        return RedirectResponse(f"/proposals/{proposal_id}", status_code=303)
    flash(request, "Declined. Nothing was changed.")
    return RedirectResponse("/proposals", status_code=303)


@router.post("/{proposal_id}/withdraw")
async def withdraw(
    proposal_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
) -> Response:
    """Withdraw your own pending request."""
    await verified_form(request)
    try:
        services.proposals.withdraw(viewer.member, proposal_id)
    except (ConcurrencyConflict, InvalidStateTransition) as exc:
        flash(request, exc.message, "error")
        return RedirectResponse(f"/proposals/{proposal_id}", status_code=303)
    flash(request, "Your request was withdrawn.")
    return RedirectResponse("/proposals", status_code=303)
