"""mDNS announce (hub) and browse (satellite/standalone) for Azimuth Photo hubs."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

log = logging.getLogger(__name__)

SERVICE_TYPE = "_azimuth._tcp.local."
_ZEROCONF_AVAILABLE = False
try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

    _ZEROCONF_AVAILABLE = True
except ImportError:  # pragma: no cover - optional until PKG lane installs it
    ServiceBrowser = ServiceInfo = ServiceStateChange = Zeroconf = None  # type: ignore

_announcer: Any = None


def zeroconf_available() -> bool:
    return bool(_ZEROCONF_AVAILABLE)


def mdns_disabled() -> bool:
    return os.environ.get("AZIMUTH_NO_MDNS", "").strip() in ("1", "true", "yes", "on")


def is_hub_mode() -> bool:
    mode = os.environ.get("AZIMUTH_MODE", "").strip().lower()
    if mode in ("satellite", "standalone"):
        return False
    if os.environ.get("AZIMUTH_HUB_URL", "").strip():
        return False
    return mode in ("", "hub")


def _local_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _candidate_ipv4s() -> list[str]:
    """Every address this machine answers on, default route first."""

    addresses = [_local_ipv4()]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
    except OSError:
        pass
    return addresses


def _accepts(host: str, port: int, *, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _reachable_ipv4(port: int) -> str:
    """The address a client can actually reach this hub on.

    The default route is not it when the server is bound to one interface —
    measured on the owner's hub, which serves only its Tailscale address while
    advertising the LAN one, so the single result a laptop found on the network
    was an address nothing was listening on. Ask the socket instead of guessing:
    uvicorn is already listening by the time startup events run.
    """

    for address in _candidate_ipv4s():
        if _accepts(address, port):
            return address
    # Nothing answered — announce the old guess rather than nothing at all.
    return _local_ipv4()


def _service_name(display_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in display_name).strip() or "Azimuth Photo"
    return f"{safe}._azimuth._tcp.local."


class HubAnnouncer:
    def __init__(self, *, name: str, port: int, hub_id: str):
        if not _ZEROCONF_AVAILABLE:
            raise RuntimeError("zeroconf is not installed")
        self.name = name
        self.port = int(port)
        self.hub_id = hub_id
        self._zc: Any = None
        self._info: Any = None

    def start(self) -> None:
        host = _reachable_ipv4(self.port)
        props = {
            b"hub_id": self.hub_id.encode("utf-8"),
            b"name": self.name.encode("utf-8"),
            b"path": b"/",
        }
        self._info = ServiceInfo(
            SERVICE_TYPE,
            _service_name(self.name),
            addresses=[socket.inet_aton(host)],
            port=self.port,
            properties=props,
            server=f"{socket.gethostname()}.local.",
        )
        self._zc = Zeroconf()
        self._zc.register_service(self._info)
        log.info("mDNS announced %s on %s:%s hub_id=%s", self.name, host, self.port, self.hub_id)

    def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        finally:
            self._zc.close()
            self._zc = None
            self._info = None


def start_hub_announce(*, name: str, port: int, hub_id: str) -> HubAnnouncer | None:
    global _announcer
    if mdns_disabled() or not is_hub_mode():
        return None
    if not _ZEROCONF_AVAILABLE:
        log.warning("AZIMUTH mDNS skipped: zeroconf not installed")
        return None
    stop_hub_announce()
    announcer = HubAnnouncer(name=name, port=port, hub_id=hub_id)
    try:
        announcer.start()
    except Exception:
        log.exception("failed to start mDNS announce")
        return None
    _announcer = announcer
    return announcer


def stop_hub_announce() -> None:
    global _announcer
    if _announcer is not None:
        try:
            _announcer.stop()
        except Exception:
            log.exception("failed to stop mDNS announce")
        _announcer = None


async def browse_hubs(*, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
    """Browse for `_azimuth._tcp.local` hubs for ~2 seconds."""
    if not _ZEROCONF_AVAILABLE:
        return []
    if mdns_disabled():
        return []

    found: dict[str, dict[str, Any]] = {}

    def _on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
            return
        info = zeroconf.get_service_info(service_type, name, timeout=500)
        if info is None:
            return
        addresses = []
        for raw in info.addresses or []:
            try:
                addresses.append(socket.inet_ntoa(raw))
            except OSError:
                continue
        if not addresses:
            return
        props = {k.decode() if isinstance(k, bytes) else str(k): (
            v.decode() if isinstance(v, bytes) else str(v)
        ) for k, v in (info.properties or {}).items()}
        hub_id = props.get("hub_id") or ""
        display = props.get("name") or name.replace("._azimuth._tcp.local.", "")
        url = f"http://{addresses[0]}:{info.port}"
        key = hub_id or url
        found[key] = {"name": display, "url": url, "hub_id": hub_id}

    zc = Zeroconf()
    try:
        browser = ServiceBrowser(zc, SERVICE_TYPE, handlers=[_on_service_state_change])
        await asyncio.sleep(timeout_seconds)
        browser.cancel()
    finally:
        zc.close()
    return list(found.values())
