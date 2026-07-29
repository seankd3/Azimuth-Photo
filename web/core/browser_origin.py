import os
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BLOCKED_CROSS_SITE_RESPONSE = {"error": "Cross-site browser request blocked"}
BLOCKED_HOST_RESPONSE = {
    "error": "Unrecognized Host header. Set AZIMUTH_ALLOWED_HOSTS to the name this library is served under."
}
ALLOWED_HOSTS_ENV = "AZIMUTH_ALLOWED_HOSTS"
BIND_HOST_ENV = "AZIMUTH_HOST"
# Name families a rebinding attacker cannot mint: loopback, mDNS, tailnet.
ALLOWED_HOST_SUFFIXES = (".localhost", ".local", ".ts.net")


class BrowserOriginGuardMiddleware:
    """Reject cross-site browser writes and rebound Hosts, leaving non-browser
    clients alone. The Host check runs first for every method, so the origin set
    below can keep deriving from a Host header that is already vetted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if not _host_is_allowed(_host_header(scope)):
            await _reject(scope, receive, send, BLOCKED_HOST_RESPONSE, status_code=400)
            return

        if scope.get("method") not in UNSAFE_METHODS:
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


async def _reject(scope, receive, send, payload=BLOCKED_CROSS_SITE_RESPONSE, status_code: int = 403) -> None:
    response = JSONResponse(payload, status_code=status_code)
    await response(scope, receive, send)


def _host_header(scope) -> str:
    # Scanned directly: this runs on every request, including thumbnail floods.
    for key, value in scope.get("headers") or []:
        if key == b"host":
            return value.decode("latin-1")
    return ""


def _configured_allowed_hosts() -> frozenset[str]:
    hosts = {
        part.strip().lower()
        for part in os.environ.get(ALLOWED_HOSTS_ENV, "").split(",")
        if part.strip()
    }
    bind = os.environ.get(BIND_HOST_ENV, "").strip().lower()
    if bind:
        hosts.add(bind)
    return frozenset(hosts)


def _host_is_allowed(raw_host: str) -> bool:
    """DNS-rebinding defense: a rebinding page can only aim a browser at us
    through a public domain the attacker owns. Accept IP literals (a browser
    never rebinds those), single-label LAN/MagicDNS names, the loopback/mDNS/
    tailnet suffixes, and whatever the operator configured; reject other names
    so a hostile Host can never approve itself as our own origin."""
    host = raw_host.strip()
    if not host:
        return True  # no Host header: not a browser
    try:
        hostname = (urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        return False
    if not hostname:
        return False
    if hostname in _configured_allowed_hosts():
        return True
    try:
        ip_address(hostname)
        return True
    except ValueError:
        pass
    return "." not in hostname or hostname.endswith(ALLOWED_HOST_SUFFIXES)


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


def _last_header_value(value: str) -> str:
    """The rightmost chain entry — the one our own trusted proxy appended.
    Everything to its left is client-supplied and therefore spoofable."""
    return value.rsplit(",", 1)[-1].strip()


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
