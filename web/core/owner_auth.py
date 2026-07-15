"""Default-deny owner authentication for every non-public route (AUTH_SPEC v1)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import JSONResponse, RedirectResponse

# The documented public surface. Everything else requires an owner credential.
PUBLIC_PATHS = frozenset(
    {
        "/unlock",           # owner unlock page
        "/api/auth/unlock",  # unlock form target
        "/api/auth/status",  # configured/unlocked state; drives the Unsecured banner
        "/api/version",      # public release metadata for clients and satellites
        "/api/pair",         # pairing-code redeem: single-use, expiring, rate-limited
        "/sw.js",            # mobile service worker (static JS served from the site root)
    }
)
PUBLIC_PREFIXES = (
    "/static/",  # css/js/img assets only
    "/s/",       # share links + published client galleries (token-scoped by the features)
)
# Public only while setup is incomplete; a fresh install has no data to protect.
SETUP_PAGE = "/setup"
SETUP_API_PREFIX = "/api/setup/"


def _setup_is_incomplete() -> bool:
    from features.pages import routes as page_routes

    return page_routes.needs_setup()


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return True
    if path == SETUP_PAGE or path.startswith(SETUP_API_PREFIX):
        return _setup_is_incomplete()
    return False


class OwnerAuthMiddleware:
    """Default-deny: unauthenticated pages redirect to /unlock, APIs get 401."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        from features.auth import service as owner_service

        if await owner_service.scope_is_owner(scope):
            await self.app(scope, receive, send)
            return

        if path.startswith("/api/"):
            response = JSONResponse({"error": "Owner authentication required"}, status_code=401)
        else:
            suffix = f"?next={quote(path, safe='/')}" if path not in ("", "/") else ""
            response = RedirectResponse(f"/unlock{suffix}", status_code=303)
        await response(scope, receive, send)
