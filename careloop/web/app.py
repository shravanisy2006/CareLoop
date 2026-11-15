"""FastAPI application factory.

Run locally with:  uvicorn careloop.web.app:create_app --factory --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from careloop.config import Settings, load_settings
from careloop.domain.errors import CareLoopError
from careloop.services.care_cycle import CareCycleRunner
from careloop.services.container import Services, build_services
from careloop.web.rendering import build_templates, render
from careloop.web.routes import (
    appointments,
    auth,
    circle,
    dashboard,
    doses,
    history,
    medications,
    preferences,
    proposals,
)
from careloop.web.security import NotSignedIn, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    settings = settings or load_settings()
    services = services or build_services(settings)
    runner = CareCycleRunner(services.cycle, settings.cycle_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="careloop_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    for module in (
        auth,
        dashboard,
        doses,
        medications,
        appointments,
        proposals,
        history,
        circle,
        preferences,
    ):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(NotSignedIn)
    async def not_signed_in(request: Request, _: NotSignedIn):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CareLoopError)
    async def careloop_error(request: Request, exc: CareLoopError):
        return render(
            request,
            "error.html",
            {"message": exc.message, "status": exc.status_code},
            status_code=exc.status_code,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        message = (
            "We could not find that page."
            if exc.status_code == 404
            else "That request could not be completed."
        )
        return render(
            request,
            "error.html",
            {"message": message, "status": exc.status_code},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, _: RequestValidationError):
        return render(
            request,
            "error.html",
            {"message": "That request was not valid.", "status": 400},
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # Log the type and route only: messages may contain user data.
        logger.error("Unhandled %s on %s", type(exc).__name__, request.url.path, exc_info=exc)
        return render(
            request,
            "error.html",
            {"message": "Something went wrong on our side. Please try again.", "status": 500},
            status_code=500,
        )

    return app
