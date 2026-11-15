"""FastAPI application factory.

Run locally with:  uvicorn careloop.web.app:create_app --factory --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response

from careloop.config import Settings, load_settings
from careloop.demo import ensure_demo_circle
from careloop.domain.errors import CareLoopError
from careloop.services.care_cycle import CareCycleRunner
from careloop.services.container import Services, build_services
from careloop.web.rendering import build_templates, render
from careloop.web.routes import (
    appointments,
    auth,
    circle,
    dashboard,
    demo,
    doses,
    history,
    medications,
    preferences,
    proposals,
)
from careloop.web.security import NotSignedIn, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
ROUTE_MODULES = (
    auth,
    dashboard,
    doses,
    medications,
    appointments,
    proposals,
    history,
    circle,
    preferences,
)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """Build the FastAPI app; tests pass their own settings and services."""
    _configure_logging()
    settings = settings or load_settings()
    services = services or build_services(settings)
    if settings.demo_mode:
        demo_circle = ensure_demo_circle(services)
        logger.warning("Demo mode is on: public demo circle %s is available.", demo_circle.code)
    runner = CareCycleRunner(services.cycle, settings.cycle_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.scheduler_enabled:
            runner.start()
        yield
        runner.stop()

    app = FastAPI(
        title="CareLoop",
        docs_url=None,  # no public API explorer for a health app
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.services = services
    app.state.templates = build_templates()
    _add_middleware(app, settings)
    _include_routes(app, settings)
    _register_error_handlers(app)
    return app


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="careloop_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.secure_cookies,
    )


def _include_routes(app: FastAPI, settings: Settings) -> None:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for module in ROUTE_MODULES:
        app.include_router(module.router)
    if settings.demo_mode:
        # The one-click demo sign-in does not exist at all outside demo mode.
        app.include_router(demo.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}


def _error_page(request: Request, message: str, status: int) -> Response:
    return render(request, "error.html", {"message": message, "status": status}, status_code=status)


async def _not_signed_in(_request: Request, _exc: Exception) -> Response:
    return RedirectResponse("/login", status_code=HTTPStatus.SEE_OTHER)


async def _careloop_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, CareLoopError):  # pragma: no cover - registered for this type
        raise exc
    return _error_page(request, exc.message, exc.status_code)


async def _http_error(request: Request, exc: Exception) -> Response:
    status = getattr(exc, "status_code", HTTPStatus.INTERNAL_SERVER_ERROR)
    message = (
        "We could not find that page."
        if status == HTTPStatus.NOT_FOUND
        else "That request could not be completed."
    )
    return _error_page(request, message, status)


async def _bad_request(request: Request, _exc: Exception) -> Response:
    return _error_page(request, "That request was not valid.", HTTPStatus.BAD_REQUEST)


async def _unexpected(request: Request, exc: Exception) -> Response:
    # Log the type and route only: exception messages may contain user data.
    logger.error("Unhandled %s on %s", type(exc).__name__, request.url.path, exc_info=exc)
    return _error_page(
        request,
        "Something went wrong on our side. Please try again.",
        HTTPStatus.INTERNAL_SERVER_ERROR,
    )


def _register_error_handlers(app: FastAPI) -> None:
    """Every error ends on a friendly page; nothing technical is shown to people."""
    app.add_exception_handler(NotSignedIn, _not_signed_in)
    app.add_exception_handler(CareLoopError, _careloop_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _bad_request)
    app.add_exception_handler(Exception, _unexpected)
