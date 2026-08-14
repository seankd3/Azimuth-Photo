"""Owner-key credentials: storage, session cookies, and per-request checks.

All crypto reuses the share-link helpers (scrypt hashes, HMAC cookies); this
module only decides *what* gets signed and which credentials count as the owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from ipaddress import ip_address

import settings
from core.browser_origin import _is_trusted_proxy, _last_header_value, _parse_forwarded
from features.auth import passwords as share_auth

COOKIE_NAME = "pa_o"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
# Same throttling budget as share unlock, but a single bucket: there is one owner.
UNLOCK_FAILURE_LIMIT = 5
UNLOCK_FAILURE_WINDOW_SECONDS = 15 * 60

_unlock_failures = {"count": 0, "first_at": 0.0}
# sha256(bearer) -> the stored hash it verified against; spares scrypt per request.
_verified_bearer_cache: dict[str, str] = {}


# --- key storage -------------------------------------------------------------

def owner_key_hash() -> str:
    return str(settings.get_settings().get("owner_key_hash") or "").strip()


def configured() -> bool:
    return bool(owner_key_hash())


def session_epoch() -> int:
    try:
        return max(0, int(settings.get_settings().get("owner_session_epoch") or 0))
    except (TypeError, ValueError):
        return 0


async def set_owner_key(key: str) -> None:
    """Store the scrypt hash; rotating an existing key bumps the session epoch,
    which invalidates every previously issued browser session (not device tokens).
    The FIRST key set also revokes every paired device: the pre-key setup window
    trusts any LAN client, so slam it shut once the owner secures the install."""
    hashed = await asyncio.to_thread(share_auth.hash_password, key)
    current = settings.get_settings()
    first_key = not str(current.get("owner_key_hash") or "").strip()
    epoch = session_epoch() + (0 if first_key else 1)
    settings.save_settings({**current, "owner_key_hash": hashed, "owner_session_epoch": epoch})
    _verified_bearer_cache.clear()
    if first_key:
        pass


async def verify_owner_key(key: str) -> bool:
    stored = owner_key_hash()
    if not stored:
        return False
    return await asyncio.to_thread(share_auth.verify_password, key, stored)


# --- session cookie ----------------------------------------------------------

def session_cookie_value() -> str:
    # Share HMAC cookie format with the epoch mixed into the signed token part.
    return share_auth.cookie_value(f"owner-e{session_epoch()}", owner_key_hash())


def set_session_cookie(response, *, request=None) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_cookie_value(),
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=share_auth.request_is_secure(request) if request is not None else False,
    )


# --- unlock throttling -------------------------------------------------------

def unlock_retry_after(now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    if _unlock_failures["count"] < UNLOCK_FAILURE_LIMIT:
        return None
    remaining = _unlock_failures["first_at"] + UNLOCK_FAILURE_WINDOW_SECONDS - now
    if remaining <= 0:
        clear_unlock_failures()
        return None
    return int(remaining) + 1


def record_unlock_failure(now: float | None = None) -> None:
    now = time.time() if now is None else now
    if not _unlock_failures["count"] or now >= _unlock_failures["first_at"] + UNLOCK_FAILURE_WINDOW_SECONDS:
        _unlock_failures.update(count=1, first_at=now)
    else:
        _unlock_failures["count"] += 1


def clear_unlock_failures() -> None:
    _unlock_failures.update(count=0, first_at=0.0)


# --- per-request credential checks -------------------------------------------

def scope_headers(scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        headers.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
    return headers


def client_ip(scope, headers: dict[str, str]) -> str:
    """Real client IP: forwarded headers count only when the peer is a trusted
    proxy (loopback or the server itself), mirroring core.browser_origin. The
    rightmost chain entry is the one the proxy appended — nginx, Caddy, Traefik
    and friends all append, so the leftmost entry is attacker-controlled."""
    client = scope.get("client") or ()
    peer = str(client[0]) if client and client[0] else ""
    if _is_trusted_proxy(scope):
        forwarded_for = _last_header_value(headers.get("x-forwarded-for", ""))
        if forwarded_for:
            return forwarded_for
        forwarded = _parse_forwarded(_last_header_value(headers.get("forwarded", ""))).get("for", "")
        if forwarded:
            return forwarded
    return peer


def is_loopback_client(scope, headers: dict[str, str]) -> bool:
    """Only true loopback is exempt — never LAN or tailnet addresses. A request
    carrying forwarded headers came through a proxy from somewhere else, so it
    is never loopback-exempt however the chain describes itself."""
    if headers.get("x-forwarded-for", "").strip() or headers.get("forwarded", "").strip():
        return False
    try:
        return ip_address(client_ip(scope, headers)).is_loopback
    except ValueError:
        return False


def has_session_cookie(headers: dict[str, str]) -> bool:
    actual = ""
    for part in headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            actual = value.strip()
            break
    if not actual:
        return False
    return hmac.compare_digest(actual, session_cookie_value())


async def _bearer_is_owner(headers: dict[str, str]) -> bool:
    scheme, _, token = headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return False
    stored = owner_key_hash()
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if _verified_bearer_cache.get(fingerprint) == stored:
        return True
    if not await verify_owner_key(token):
        return False
    if len(_verified_bearer_cache) >= 32:
        _verified_bearer_cache.clear()
    _verified_bearer_cache[fingerprint] = stored
    return True


async def _device_token_is_owner(headers: dict[str, str]) -> bool:
    # Device pairing died with the hub machinery (2026-08-14 gutting order).
    del headers
    return False


async def scope_is_owner(scope, headers: dict[str, str] | None = None) -> bool:
    """AUTH_SPEC: any of no-key-configured (legacy install), session cookie,
    true-loopback client, paired-device token, or the owner key as a bearer."""
    if not configured():
        return True
    if headers is None:
        headers = scope_headers(scope)
    if has_session_cookie(headers):
        return True
    if is_loopback_client(scope, headers):
        return True
    if await _device_token_is_owner(headers):
        return True
    return await _bearer_is_owner(headers)
