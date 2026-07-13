"""Consistent, safe failure responses for the public application surface."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException


log = logging.getLogger(__name__)
SERVER_ERROR_MESSAGE = "Something went wrong on the server. Your photos are safe — try again."
VALIDATION_ERROR_MESSAGE = "Please check the information and try again."


def _message(detail) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail
    return "The request could not be completed."


def install_error_handlers(
    app: FastAPI,
    *,
    not_found_response: Callable[[Request], Response],
) -> None:
    """Install API envelopes while preserving a calm HTML page for unknown URLs."""

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": VALIDATION_ERROR_MESSAGE}, status_code=422)
        return JSONResponse({"error": VALIDATION_ERROR_MESSAGE}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": _message(exc.detail)},
                status_code=exc.status_code,
                headers=getattr(exc, "headers", None),
            )
        if exc.status_code == 404:
            return not_found_response(request)
        return JSONResponse({"error": _message(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        log.exception("Unhandled request failure for %s", request.url.path, exc_info=exc)
        return JSONResponse({"error": SERVER_ERROR_MESSAGE}, status_code=500)
