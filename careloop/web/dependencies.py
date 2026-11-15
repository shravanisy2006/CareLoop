"""FastAPI dependencies shared by the route modules."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from starlette.requests import Request

from careloop.domain.models import Circle, Member
from careloop.services.container import Services
from careloop.web.security import SESSION_CIRCLE, SESSION_MEMBER, NotSignedIn


def get_services(request: Request) -> Services:
    """The service container created by `create_app`."""
    services: Services = request.app.state.services
    return services


@dataclass(frozen=True)
class Viewer:
    """The signed-in member and their care circle, re-loaded on every request."""

    member: Member
    circle: Circle


def get_viewer(request: Request, services: Services = Depends(get_services)) -> Viewer:
    """Return the signed-in member and circle, or raise NotSignedIn."""
    member = services.auth.current_member(
        request.session.get(SESSION_MEMBER), request.session.get(SESSION_CIRCLE)
    )
    if member is None:
        request.session.clear()
        raise NotSignedIn()
    circle = services.ctx.repo.get_circle(member.circle_id)
    if circle is None:
        request.session.clear()
        raise NotSignedIn()
    return Viewer(member=member, circle=circle)


def member_names(services: Services, viewer: Viewer) -> dict[str, str]:
    """Map member id to display name for everyone in the viewer's circle.

    Removed members are included, so histories can still show who did what.
    """
    return {m.id: m.display_name for m in services.circles.list_members(viewer.member)}
