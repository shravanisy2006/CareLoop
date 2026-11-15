"""Home page: the elder's simple "Today" screen, or the care team overview."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from careloop.domain.models import AppointmentStatus, DoseStatus, ProposalStatus, Role
from careloop.services.container import Services
from careloop.web.dependencies import Viewer, get_services, get_viewer
from careloop.web.rendering import render
from careloop.web.security import SESSION_MEMBER

router = APIRouter()


def _upcoming_appointments(services: Services, viewer: Viewer, limit: int = 3):
    now = services.ctx.clock.now()
    return [
        a
        for a in services.records.list_appointments(viewer.member)
        if a.status is AppointmentStatus.SCHEDULED and a.details.starts_at > now
    ][:limit]


@router.get("/")
def home(request: Request, services: Services = Depends(get_services)):
    if not request.session.get(SESSION_MEMBER):
        return render(request, "auth/landing.html")
    viewer = get_viewer(request, services)
    member, circle = viewer.member, viewer.circle

    doses = services.doses.today(member, circle)
    now = services.ctx.clock.now()
    context = {
        "viewer": viewer,
        "due_doses": [
            v
            for v in doses
            if v.is_open and v.dose.scheduled_for <= now and v.dose.status is DoseStatus.SCHEDULED
        ],
        "later_doses": [
            v for v in doses if v.dose.status is DoseStatus.SCHEDULED and v.dose.scheduled_for > now
        ],
        "done_doses": [v for v in doses if v.dose.status is not DoseStatus.SCHEDULED],
        "appointments": _upcoming_appointments(services, viewer),
        "pending_proposals": services.proposals.list(member, ProposalStatus.PENDING),
        "adherence": services.doses.adherence(member, circle, days=7),
        "now": now,
    }
    if member.role is Role.ELDER:
        return render(request, "dashboard_elder.html", context)
    context["recent_notifications"] = services.history.notifications(member, limit=8)
    return render(request, "dashboard_team.html", context)
