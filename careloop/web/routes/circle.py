"""People in the care circle and escalation settings (elder only to change)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from careloop.domain.errors import InvalidStateTransition, ValidationFailed
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.forms import form_values
from careloop.web.rendering import flash, render
from careloop.web.security import verified_form

router = APIRouter(prefix="/circle")


def _page(
    request,
    services,
    viewer,
    *,
    member_values=None,
    member_errors=None,
    policy_values=None,
    policy_errors=None,
    status_code=200,
):
    policy = viewer.circle.policy
    return render(
        request,
        "circle/manage.html",
        {
            "viewer": viewer,
            "members": [m for m in services.circles.list_members(viewer.member) if m.active],
            "member_values": member_values or {},
            "member_errors": member_errors or {},
            "policy_values": policy_values
            or {
                "caretaker_after_minutes": policy.caretaker_after_minutes,
                "family_after_minutes": policy.family_after_minutes,
                "close_after_minutes": policy.close_after_minutes,
            },
            "policy_errors": policy_errors or {},
        },
        status_code=status_code,
    )


@router.get("")
def manage(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    return _page(request, services, viewer)


@router.post("/members")
async def add_member(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    values = form_values(form)
    try:
        member = services.circles.add_member(
            viewer.member,
            display_name=values.get("display_name", ""),
            role=values.get("role", ""),
            email=values.get("email", ""),
            passcode=values.get("passcode", ""),
        )
    except ValidationFailed as exc:
        values.pop("passcode", None)
        return _page(
            request,
            services,
            viewer,
            member_values=values,
            member_errors=exc.field_errors,
            status_code=422,
        )
    flash(
        request,
        f"{member.display_name} was added. "
        "Share the circle code and their passcode with them in person.",
    )
    return RedirectResponse("/circle", status_code=303)


@router.post("/members/{member_id}/remove")
async def remove_member(
    member_id: str,
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    await verified_form(request)
    try:
        services.circles.remove_member(viewer.member, member_id)
    except InvalidStateTransition as exc:
        flash(request, exc.message, "error")
    else:
        flash(request, "That person no longer has access.")
    return RedirectResponse("/circle", status_code=303)


@router.post("/settings")
async def update_settings(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    form = await verified_form(request)
    values = form_values(form)
    try:
        services.circles.update_policy(
            viewer.member,
            caretaker=values.get("caretaker_after_minutes", ""),
            family=values.get("family_after_minutes", ""),
            close=values.get("close_after_minutes", ""),
        )
    except ValidationFailed as exc:
        return _page(
            request,
            services,
            viewer,
            policy_values=values,
            policy_errors=exc.field_errors,
            status_code=422,
        )
    flash(request, "Reminder timings were saved.")
    return RedirectResponse("/circle", status_code=303)
