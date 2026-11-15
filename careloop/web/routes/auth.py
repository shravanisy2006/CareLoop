"""Sign in, sign out and starting a new care circle."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import AccountLocked, AuthenticationFailed, ValidationFailed
from careloop.services.container import Services
from careloop.web.dependencies import get_services
from careloop.web.forms import form_values
from careloop.web.rendering import flash, render
from careloop.web.security import SESSION_MEMBER, sign_in, sign_out, verified_form

router = APIRouter()

TIMEZONE_CHOICES = (
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
    "UTC",
)


@router.get("/login")
def login_page(request: Request) -> Response:
    """Show the sign-in form (or go home if already signed in)."""
    if request.session.get(SESSION_MEMBER):
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/login.html", {"values": {}})


@router.post("/login")
async def login(request: Request, services: Services = Depends(get_services)) -> Response:
    """Sign in with circle code, name and passcode."""
    form = await verified_form(request)
    values = form_values(form)
    try:
        member = services.auth.sign_in(
            values.get("circle_code", ""),
            values.get("display_name", ""),
            values.get("passcode", ""),
        )
    except (AuthenticationFailed, AccountLocked) as exc:
        values.pop("passcode", None)
        return render(
            request,
            "auth/login.html",
            {"values": values, "error": exc.message},
            status_code=exc.status_code,
        )
    sign_in(request, member)
    flash(request, f"Welcome, {member.display_name}.")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> Response:
    """End the session."""
    await verified_form(request)
    sign_out(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/start")
def start_page(request: Request, services: Services = Depends(get_services)) -> Response:
    """Show the form to start a new care circle."""
    return render(
        request,
        "auth/start.html",
        {
            "values": {"timezone": services.ctx.settings.default_timezone},
            "timezones": TIMEZONE_CHOICES,
            "errors": {},
        },
    )


@router.post("/start")
async def start(request: Request, services: Services = Depends(get_services)) -> Response:
    """Create a care circle with the elder as its first member, and sign them in."""
    form = await verified_form(request)
    values = form_values(form)
    try:
        circle, elder = services.circles.create_circle(
            elder_name=values.get("elder_name", ""),
            passcode=values.get("passcode", ""),
            confirm_passcode=values.get("confirm_passcode", ""),
            email=values.get("email", ""),
            timezone=values.get("timezone", ""),
        )
    except ValidationFailed as exc:
        values.pop("passcode", None)
        values.pop("confirm_passcode", None)
        return render(
            request,
            "auth/start.html",
            {"values": values, "timezones": TIMEZONE_CHOICES, "errors": exc.field_errors},
            status_code=422,
        )
    sign_in(request, elder)
    request.session["new_circle_code"] = circle.code
    return RedirectResponse("/welcome", status_code=303)


@router.get("/welcome")
def welcome(request: Request) -> Response:
    """Show the new circle's code once, right after it is created."""
    code = request.session.pop("new_circle_code", None)
    if not code:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/welcome.html", {"circle_code": code})
