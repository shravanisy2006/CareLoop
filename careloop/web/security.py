"""Web security: session sign-in state, CSRF protection and response headers.

* Sessions are signed cookies (itsdangerous via Starlette), HttpOnly,
  SameSite=Lax, and Secure when CARELOOP_SECURE_COOKIES=true. They hold only
  the member id, circle id and CSRF token - never health data.
* Every POST must carry the session's CSRF token in a hidden form field.
* The site uses no JavaScript, so the Content-Security-Policy forbids scripts
  entirely.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.datastructures import FormData
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from careloop.domain.errors import CareLoopError
from careloop.domain.models import Member

SESSION_MEMBER = "member_id"
SESSION_CIRCLE = "circle_id"
SESSION_CSRF = "csrf"
CSRF_FIELD = "csrf_token"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'; script-src 'none'"
)


class CsrfFailed(CareLoopError):
    status_code = 403
    default_message = "This form expired. Please go back, reload the page and try again."


class NotSignedIn(Exception):
    """Raised by routes that need a signed-in member; handled as a redirect."""


def csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF] = token
    return token


async def verified_form(request: Request) -> FormData:
    """Read a POSTed form and check its CSRF token in constant time."""
    form = await request.form()
    expected = request.session.get(SESSION_CSRF, "")
    submitted = str(form.get(CSRF_FIELD, ""))
    if not expected or not hmac.compare_digest(expected, submitted):
        raise CsrfFailed()
    return form


def safe_redirect_target(value: object, default: str = "/") -> str:
    """Only allow redirects to paths on this site (blocks open redirects)."""
    target = str(value or "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def sign_in(request: Request, member: Member) -> None:
    """Start a fresh session (prevents session fixation) for `member`."""
    text_size = request.session.get("text_size")
    request.session.clear()
    if text_size:
        request.session["text_size"] = text_size
    request.session[SESSION_MEMBER] = member.id
    request.session[SESSION_CIRCLE] = member.circle_id
    request.session[SESSION_CSRF] = secrets.token_urlsafe(32)


def sign_out(request: Request) -> None:
    text_size = request.session.get("text_size")
    request.session.clear()
    if text_size:
        request.session["text_size"] = text_size


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/static/"):
            # Pages contain health information: never cache them.
            response.headers["Cache-Control"] = "no-store"
        return response
