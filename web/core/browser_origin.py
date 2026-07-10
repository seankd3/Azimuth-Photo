from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BLOCKED_CROSS_SITE_RESPONSE = {"error": "Cross-site browser request blocked"}


class BrowserOriginGuardMiddleware:
    """Reject cross-site browser writes while leaving non-browser clients alone."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        fetch_site = headers.get("sec-fetch-site", "").strip().lower()
        if fetch_site == "cross-site":
            await _reject(scope, receive, send)
            return

        origin_header = headers.get("origin", "").strip()
        if origin_header:
            origin = _normalize_origin(origin_header)
            if origin is None or origin not in _request_origins(scope, headers):
                await _reject(scope, receive, send)
                return

        await self.app(scope, receive, send)


async def _reject(scope, receive, send) -> None:
    response = JSONResponse(BLOCKED_CROSS_SITE_RESPONSE, status_code=403)
    await response(scope, receive, send)


def _headers(scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers") or []
    }


def _request_origins(scope, headers: dict[str, str]) -> set[str]:
    origins: set[str] = set()
    host = _first_header_value(headers.get("host", ""))
    scheme = str(scope.get("scheme") or "http")

    if _is_trusted_proxy(scope):
        forwarded = _parse_forwarded(headers.get("forwarded", ""))
        if forwarded.get("host"):
            _add_origin(origins, forwarded.get("proto") or scheme, forwarded["host"])

        forwarded_host = _first_header_value(headers.get("x-forwarded-host", ""))
        forwarded_proto = _first_header_value(headers.get("x-forwarded-proto", ""))
        if forwarded_host:
            _add_origin(origins, forwarded_proto or scheme, forwarded_host)
        if forwarded_proto and host:
            _add_origin(origins, forwarded_proto, host)

    if host:
        _add_origin(origins, scheme, host)

    server = scope.get("server") or ()
    if len(server) >= 2 and server[0]:
        _add_origin(origins, scheme, f"{server[0]}:{server[1]}")

    return origins


def _is_trusted_proxy(scope) -> bool:
    client = scope.get("client") or ()
    if not client or not client[0]:
        return False
    try:
        client_address = ip_address(client[0])
    except ValueError:
        return False
    if client_address.is_loopback:
        return True

    server = scope.get("server") or ()
    if not server or not server[0]:
        return False
    try:
        return client_address == ip_address(server[0])
    except ValueError:
        return False


def _parse_forwarded(value: str) -> dict[str, str]:
    first = _first_header_value(value)
    parsed: dict[str, str] = {}
    for part in first.split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        parsed[key.strip().lower()] = raw.strip().strip('"')
    return parsed


def _first_header_value(value: str) -> str:
    return value.split(",", 1)[0].strip()


def _add_origin(origins: set[str], scheme: str, host: str) -> None:
    origin = _normalize_origin(f"{scheme}://{host}")
    if origin:
        origins.add(origin)


def _normalize_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        return None
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    host = f"{hostname}:{port}" if port else hostname
    return f"{scheme}://{host}"
