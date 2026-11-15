"""Dose history with adherence, the audit trail, and the email log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.rendering import render

router = APIRouter()


def _names(services: Services, viewer: Viewer) -> dict[str, str]:
    return {m.id: m.display_name for m in services.circles.list_members(viewer.member)}


@router.get("/history")
def dose_history(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    return render(
        request,
        "history/doses.html",
        {
            "viewer": viewer,
            "days": days,
            "doses": services.doses.history(viewer.member, viewer.circle, days),
            "adherence": services.doses.adherence(viewer.member, viewer.circle, days),
            "names": _names(services, viewer),
        },
    )


@router.get("/activity")
def activity(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    return render(
        request,
        "history/activity.html",
        {
            "viewer": viewer,
            "events": services.history.audit_events(viewer.member),
            "names": _names(services, viewer),
        },
    )


@router.get("/notifications")
def notifications(
    request: Request,
    services: Services = Depends(get_services),
    viewer: Viewer = Depends(get_viewer),
):
    return render(
        request,
        "history/notifications.html",
        {
            "viewer": viewer,
            "notifications": services.history.notifications(viewer.member),
            "names": _names(services, viewer),
            "email_configured": services.ctx.settings.email_configured,
        },
    )
