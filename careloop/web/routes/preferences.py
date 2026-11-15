"""Display preferences that help older eyes, such as text size.

Stored in the signed session cookie (and kept across sign-in), so it works
on the sign-in page too and needs no JavaScript.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from careloop.web.security import safe_redirect_target, verified_form

router = APIRouter()

TEXT_SIZES = ("normal", "large", "xlarge")
TEXT_SIZE_LABELS = {"normal": "Normal", "large": "Large", "xlarge": "Extra large"}
SESSION_TEXT_SIZE = "text_size"


def text_size(request: Request) -> str:
    """The visitor's chosen text size, defaulting to normal."""
    value = request.session.get(SESSION_TEXT_SIZE, "normal")
    return value if value in TEXT_SIZES else "normal"


@router.post("/text-size")
async def set_text_size(request: Request) -> Response:
    """Save the chosen text size and return to the same page."""
    form = await verified_form(request)
    size = str(form.get("size", ""))
    if size in TEXT_SIZES:
        request.session[SESSION_TEXT_SIZE] = size
    return RedirectResponse(safe_redirect_target(form.get("next")), status_code=303)
