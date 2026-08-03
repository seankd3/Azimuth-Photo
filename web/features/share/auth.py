"""Password and cookie helpers for public share links."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import Response

import settings
from core.requests import RequestBodyTooLarge, read_body_limited

COOKIE_NAME = "pa_s"
VIEW_COOKIE_NAME = "pa_v"
VISITOR_COOKIE_NAME = "pa_sv"
SECRET_ENV = "AZIMUTH_SHARE_SECRET"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
UNLOCK_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
VIEW_MAX_AGE_SECONDS = 30 * 60
FORM_BODY_MAX_BYTES = 1024


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        str(pw).encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        method, salt_b64, digest_b64 = stored.split("$", 2)
        if method != "scrypt":
            return False
        expected = _unb64(digest_b64)
        actual = hashlib.scrypt(
            str(pw).encode("utf-8"),
            salt=_unb64(salt_b64),
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def cookie_secret() -> str:
    env_secret = os.environ.get(SECRET_ENV)
    if env_secret:
        return env_secret

    current = settings.get_settings()
    existing = str(current.get("share_cookie_secret") or "").strip()
    if existing:
        return existing

    generated = secrets.token_urlsafe(32)
    settings.save_settings({**current, "share_cookie_secret": generated})
    return generated


def cookie_value(token: str, password_hash: str) -> str:
    password_fingerprint = hashlib.sha256(password_hash.encode("utf-8")).hexdigest()
    payload = f"{token}:{password_fingerprint}".encode("utf-8")
    return hmac.new(cookie_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def request_is_secure(request: Request) -> bool:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"


async def read_form_password(request: Request) -> str | None:
    """Return the small URL-encoded unlock password, or None when oversized."""

    try:
        body = await read_body_limited(request, FORM_BODY_MAX_BYTES)
    except RequestBodyTooLarge:
        return None
    decoded = body.decode("utf-8", errors="replace")
    return str((parse_qs(decoded).get("password") or [""])[0])


def share_cookie_path(token: str) -> str:
    return f"/s/{token}"


def set_unlock_cookie(response: Response, token: str, value: str, *, request: Request | None = None) -> None:
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=UNLOCK_MAX_AGE_SECONDS,
        path=share_cookie_path(token),
        httponly=True,
        samesite="lax",
        secure=request_is_secure(request) if request is not None else False,
    )


def set_view_cookie(
    response: Response,
    token: str,
    *,
    request: Request | None = None,
    path: str | None = None,
) -> None:
    response.set_cookie(
        VIEW_COOKIE_NAME,
        "1",
        max_age=VIEW_MAX_AGE_SECONDS,
        path=path or share_cookie_path(token),
        httponly=True,
        samesite="lax",
        secure=request_is_secure(request) if request is not None else False,
    )


def visitor_id(request: Request) -> str | None:
    """Return this browser's anonymous share visitor id, if it has one."""

    value = (request.cookies.get(VISITOR_COOKIE_NAME) or "").strip()
    if len(value) != 32 or any(not (character.isalnum() or character in "-_") for character in value):
        return None
    return value


def new_visitor_id() -> str:
    return secrets.token_urlsafe(24)


def set_visitor_cookie(
    response: Response,
    token: str,
    visitor: str,
    *,
    request: Request | None = None,
) -> None:
    response.set_cookie(
        VISITOR_COOKIE_NAME,
        visitor,
        max_age=UNLOCK_MAX_AGE_SECONDS,
        path=share_cookie_path(token),
        httponly=True,
        samesite="lax",
        secure=request_is_secure(request) if request is not None else False,
    )


def visitor_label(visitor: str) -> str:
    return "legacy" if visitor == "legacy" else visitor[:8]


def is_unlocked(request: Request, share: dict | None) -> bool:
    password_hash = (share or {}).get("password_hash")
    if not password_hash:
        return True
    token = str((share or {}).get("token") or "")
    expected = cookie_value(token, password_hash)
    actual = request.cookies.get(COOKIE_NAME) or ""
    return hmac.compare_digest(actual, expected)
