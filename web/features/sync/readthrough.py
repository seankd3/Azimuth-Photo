"""Satellite read-through helpers for Develop base artifacts.

This module deliberately owns only the satellite side of the frozen
``GET /api/sync/base/{content_hash}`` contract.  It does not replicate catalog
data or decode originals: a missing local original can be developed from the
hub's already-generated PABASE1 cache instead.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import sqlite3
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from features.sync import satellite


_HASH_LENGTH = 32  # BLAKE2b-128, hex encoded.
_BASE_MAGIC = b"PABASE1\0"
_BASE_HEADER_BYTES = 16
_DEFAULT_TIMEOUT_SECONDS = 5.0


class BaseReadthroughError(RuntimeError):
    """The hub could not provide a usable Develop base artifact."""


def is_satellite_mode() -> bool:
    return satellite.is_satellite_mode()


def hub_url() -> str | None:
    return satellite.hub_url() or None


def can_read_through() -> bool:
    return is_satellite_mode() and hub_url() is not None


def _content_hash_for_image(image_id: int, db_path: str) -> str | None:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT content_hash FROM images WHERE id = ?", (int(image_id),)
            ).fetchone()
    except sqlite3.Error as exc:
        raise BaseReadthroughError("Satellite content hashes are unavailable locally") from exc
    if row is None or not isinstance(row[0], str):
        return None
    value = row[0].strip().lower()
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def _request(url: str, *, timeout: float) -> tuple[bytes, str, dict[str, str]]:
    headers = {"Accept": "multipart/mixed, application/gzip, application/json"}
    headers.update(satellite.device_auth_headers())
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - hub URL is user configuration.
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.read(), response.headers.get_content_type(), headers
    except HTTPError as exc:
        if exc.code == 404:
            raise BaseReadthroughError("Hub has no cached Develop base for this photo") from exc
        raise BaseReadthroughError(f"Hub rejected the Develop base request ({exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BaseReadthroughError("Could not reach the hub for this Develop base") from exc


def _json_metadata(value: bytes | str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise BaseReadthroughError("Hub returned invalid Develop base metadata") from exc
    if not isinstance(parsed, dict):
        raise BaseReadthroughError("Hub returned invalid Develop base metadata")
    return parsed


def _multipart_parts(body: bytes, content_type: str) -> tuple[bytes, dict[str, Any]]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    binary: bytes | None = None
    metadata: dict[str, Any] | None = None
    for part in message.iter_parts():
        payload = part.get_payload(decode=True) or b""
        filename = (part.get_filename() or "").lower()
        media_type = part.get_content_type()
        if filename.endswith(".json") or media_type == "application/json":
            metadata = _json_metadata(payload)
        elif filename.endswith(".gz") or media_type in {"application/gzip", "application/octet-stream"}:
            binary = payload
    if binary is None or metadata is None:
        raise BaseReadthroughError("Hub returned an incomplete Develop base artifact")
    return binary, metadata


def _decode_response(base_url: str, body: bytes, content_type: str, headers: dict[str, str], *, timeout: float) -> tuple[bytes, dict[str, Any]]:
    if content_type.startswith("multipart/"):
        return _multipart_parts(body, headers.get("content-type", content_type))
    if content_type == "application/json":
        payload = _json_metadata(body)
        encoded = payload.get("base")
        metadata = payload.get("metadata")
        if isinstance(encoded, str) and isinstance(metadata, dict):
            try:
                return base64.b64decode(encoded, validate=True), metadata
            except ValueError as exc:
                raise BaseReadthroughError("Hub returned an invalid Develop base payload") from exc
        raise BaseReadthroughError("Hub returned an incomplete Develop base artifact")

    encoded_metadata = headers.get("x-photoarchive-base-metadata")
    if encoded_metadata:
        return body, _json_metadata(encoded_metadata)

    metadata_body, metadata_type, _metadata_headers = _request(f"{base_url}/meta", timeout=timeout)
    if metadata_type != "application/json":
        raise BaseReadthroughError("Hub returned invalid Develop base metadata")
    return body, _json_metadata(metadata_body)


def _validate_base(binary: bytes) -> None:
    try:
        payload = gzip.decompress(binary)
    except OSError as exc:
        raise BaseReadthroughError("Hub returned a corrupt Develop base") from exc
    if len(payload) < _BASE_HEADER_BYTES or payload[:8] != _BASE_MAGIC:
        raise BaseReadthroughError("Hub returned an invalid Develop base")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_preview(binary: bytes, preview_path: Path) -> None:
    payload = gzip.decompress(binary)
    width = int.from_bytes(payload[8:12], "little")
    height = int.from_bytes(payload[12:16], "little")
    expected = _BASE_HEADER_BYTES + width * height * 3 * 2
    if width <= 0 or height <= 0 or len(payload) != expected:
        raise BaseReadthroughError("Hub returned an invalid Develop base")
    linear = np.frombuffer(payload, dtype="<u2", offset=_BASE_HEADER_BYTES).reshape(height, width, 3).astype(np.float32) / 65535.0
    encoded = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(np.clip(linear, 0.0, 1.0), 1.0 / 2.4) - 0.055)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".jpg", dir=preview_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        Image.fromarray(np.rint(np.clip(encoded, 0.0, 1.0) * 255.0).astype(np.uint8), mode="RGB").save(temporary, "JPEG", quality=88)
        os.replace(temporary, preview_path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_base_cache_for_image(image_id: int, paths, *, db_path: str, source_path: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Fetch and atomically materialize the hub base for one local catalog row.

    Returns ``None`` when the row has no usable content hash, allowing callers
    to retain their normal local-decode failure.  Network and artifact failures
    deliberately raise an honest, user-safe ``BaseReadthroughError``.
    """

    if not can_read_through():
        return None
    content_hash = _content_hash_for_image(image_id, db_path)
    if content_hash is None:
        return None
    endpoint = f"{hub_url()}/api/sync/base/{quote(content_hash, safe='')}"
    body, content_type, headers = _request(endpoint, timeout=timeout)
    binary, metadata = _decode_response(endpoint, body, content_type, headers, timeout=timeout)
    _validate_base(binary)
    metadata = dict(metadata)
    metadata["source_path"] = str(source_path)
    _write_atomic(Path(paths.binary), binary)
    _write_atomic(Path(paths.metadata), json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
    _write_preview(binary, Path(paths.preview))
    return metadata
