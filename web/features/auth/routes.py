"""Owner unlock page, session unlock, auth status, and key set/rotate routes."""

from __future__ import annotations

import asyncio
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from features.auth import service
from features.share import auth as share_auth

router = APIRouter(tags=["auth"])
_templates: Jinja2Templates | None = None

MAX_UNLOCK_PASSWORD_LENGTH = 256
MIN_PASSPHRASE_LENGTH = 8
GENERATED_KEY_BYTES = 24  # token_urlsafe(24) -> 32 url-safe chars


class OwnerKeyBody(BaseModel):
    key: str | None = Field(
        default=None,
        min_length=MIN_PASSPHRASE_LENGTH,
        max_length=MAX_UNLOCK_PASSWORD_LENGTH,
    )
    generate: bool = False


def configure(*, templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates


def _safe_next(raw: str | None) -> str:
    value = str(raw or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _unlock_page_response(
    request: Request,
    *,
    unlock_error: bool = False,
    throttle_seconds: int | None = None,
    status_code: int = 200,
):
    if _templates is None:
        raise RuntimeError("Auth routes are not configured")
    response = _templates.TemplateResponse(
        request,
        "unlock.html",
        {
            "unlock_error": unlock_error,
            "throttle_seconds": throttle_seconds,
            "throttle_minutes": ((throttle_seconds or 0) + 59) // 60 if throttle_seconds else None,
            "next_target": _safe_next(request.query_params.get("next")),
        },
        status_code=status_code,
    )
    if throttle_seconds is not None:
        response.headers["Retry-After"] = str(throttle_seconds)
    return response


@router.get("/unlock", response_class=HTMLResponse)
async def unlock_page(request: Request):
    if not service.configured() or await service.scope_is_owner(request.scope):
        return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
    return _unlock_page_response(request, unlock_error=request.query_params.get("e") == "1")


@router.post("/api/auth/unlock")
async def api_auth_unlock(request: Request):
    """Mirrors share unlock: small form body, throttled, redirect with cookie."""
    retry_after = service.unlock_retry_after()
    if retry_after is not None:
        return _unlock_page_response(request, throttle_seconds=retry_after, status_code=429)
    next_target = _safe_next(request.query_params.get("next"))
    password = await share_auth.read_form_password(request)
    if (
        not password
        or len(password) > MAX_UNLOCK_PASSWORD_LENGTH
        or not await service.verify_owner_key(password)
    ):
        service.record_unlock_failure()
        await asyncio.sleep(0.4)
        return RedirectResponse(f"/unlock?e=1&next={quote(next_target, safe='/')}", status_code=303)
    service.clear_unlock_failures()
    response = RedirectResponse(next_target, status_code=303)
    service.set_session_cookie(response, request=request)
    return response


@router.get("/api/auth/status")
async def api_auth_status(request: Request):
    """`configured: false` drives the "Unsecured" banner on legacy installs."""
    return {
        "configured": service.configured(),
        "unlocked": await service.scope_is_owner(request.scope),
    }


@router.post("/api/auth/key")
async def api_auth_set_key(body: OwnerKeyBody, request: Request):
    # Owner-or-loopback only. On a fresh install (no key yet) every caller
    # counts as the owner, which is what lets the setup wizard mint the key.
    if not await service.scope_is_owner(request.scope):
        return JSONResponse({"error": "Owner authentication required"}, status_code=401)
    generated = secrets.token_urlsafe(GENERATED_KEY_BYTES) if body.generate or not body.key else None
    rotated = service.configured()
    await service.set_owner_key(generated or body.key)
    payload = {"ok": True, "configured": True, "rotated": rotated}
    if generated:
        payload["key"] = generated  # shown once, never retrievable again
    response = JSONResponse(payload)
    # Whoever just set the key is the owner at the keyboard: start their session.
    service.set_session_cookie(response, request=request)
    return response
