"""Read-only publication API and compiled web application."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .data_api import read_current, router
from .settings import Settings
from .web import mount_web

logger = logging.getLogger(__name__)


def error_response(status, code, message, headers=None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers={**(headers or {}), "Cache-Control": "no-store"},
    )


def create_app(settings=None):
    """Construct an app without requiring an active publication at startup."""
    application = FastAPI(
        title="VOC data-state API",
        version="0.1.0",
        redoc_url=None,
    )
    application.state.settings = (
        settings if settings is not None else Settings.from_env()
    )
    application.include_router(router)

    @application.middleware("http")
    async def prevent_stale_selection(request, call_next):
        # A browser must resolve the active pointer again on a new page load.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        if isinstance(
            error.detail,
            dict,
        ):
            code, message = error.detail["code"], error.detail["message"]
        else:
            code, message = {
                404: ("route_not_found", "This route does not exist."),
                405: ("method_not_allowed", "This method is not allowed."),
            }.get(
                error.status_code,
                ("http_error", "The request could not be completed."),
            )
        return error_response(
            error.status_code,
            code,
            message,
            error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return error_response(
            422,
            "invalid_request",
            "Invalid request parameters.",
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request, error):
        # Diagnostic detail stays in server logs, never in the public response.
        logger.exception("Unexpected publication API failure")
        return error_response(
            500,
            "internal_error",
            "The server could not complete the request.",
        )

    @application.get(
        "/health/live",
        tags=["Health"],
    )
    def live():
        """Confirm the HTTP process can respond independently of stored data."""
        return {"status": "alive"}

    @application.get(
        "/health/ready",
        tags=["Health"],
    )
    def ready(request: Request):
        """Confirm the active package is readable and compatible right now."""
        selected = read_current(request.app.state.settings)
        return {"status": "ready", "publication_id": selected["publication_id"]}

    # Register future API/MCP routes before this final static mount.
    mount_web(
        application,
        application.state.settings.web_dir,
    )
    return application


app = create_app()
