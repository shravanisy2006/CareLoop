"""One-click demo sign-in. Only mounted when CARELOOP_DEMO_MODE=true, so it does
not exist at all in a real deployment."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from careloop.demo import DEMO_CIRCLE_CODE, demo_person
from careloop.domain.errors import NotFound
from careloop.services.container import Services
from careloop.web.dependencies import get_services
from careloop.web.rendering import flash
from careloop.web.security import sign_in, verified_form

router = APIRouter()


@router.post("/demo/sign-in")
async def demo_sign_in(request: Request, services: Services = Depends(get_services)):
    form = await verified_form(request)
    person = demo_person(str(form.get("role", "")))
    if person is None:
        raise NotFound()
    # Goes through the normal sign-in, so passcode checks and lockout still apply.
    member = services.auth.sign_in(DEMO_CIRCLE_CODE, person.name, person.passcode)
    sign_in(request, member)
    flash(request, f"You are viewing the demo as {member.display_name}.")
    return RedirectResponse("/", status_code=303)
