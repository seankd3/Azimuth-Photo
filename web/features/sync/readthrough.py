"""Satellite read-through helpers for Develop base artifacts.

This module deliberately owns only the satellite side of the frozen
``GET /api/sync/base/{content_hash}`` contract.  It does not replicate catalog
data or decode originals: a missing local original can be developed from the
hub's already-generated PABASE1 cache instead.

Request-path paint never awaits the hub: ``fetch_base_cache_for_image`` returns
any already-local base immediately and warms a miss in the background. Blocking
hub I/O stays behind ``blocking=True`` for dedicated sync/background workers.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import sqlite3
import tempfile
import threading
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


from data import connection as data_connection
from features.sync import satellite


_HASH_LENGTH = 32  # BLAKE2b-128, hex encoded.
_BASE_MAGIC = b"PABASE1\0"
_BASE_HEADER_BYTES = 16
_DEFAULT_TIMEOUT_SECONDS = 5.0
log = logging.getLogger(__name__)

_warm_lock = threading.Lock()
_warm_inflight: set[int] = set()


class BaseReadthroughError(RuntimeError):
    """The hub could not provide a usable Develop base artifact."""


def is_satellite_mode() -> bool:
    return satellite.is_satellite_mode()


def hub_url() -> str | None:
    return satellite.hub_url() or None


def can_read_through() -> bool:
    return is_satellite_mode() and hub_url() is not None


def _open_hub_stream(endpoint: str, *, accept: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS):
    if not can_read_through():
        return None
    headers = {"Accept": accept}
    headers.update(satellite.hub_request_headers())
    request = Request(f"{hub_url()}{endpoint}", headers=headers)
    try:
        return urlopen(request, timeout=timeout)  # noqa: S310 - configured private hub URL.
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


def open_hub_original(hub_image_id: int, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS):
    """Open an authenticated streaming response for one hub-owned original."""

    remote_id = int(hub_image_id or 0)
    if remote_id <= 0:
        return None
    return _open_hub_stream(
        f"/api/sync/original/{remote_id}",
        accept="application/octet-stream",
        timeout=timeout,
    )


def open_hub_preview(
    hub_image_id: int,
    size: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
):
    """Open a hub preview stream for ZIP delivery of a mirrored gallery row."""

    remote_id = int(hub_image_id or 0)
    if remote_id <= 0 or size not in {"sm", "md", "lg"}:
        return None
    return _open_hub_stream(
        f"/api/thumb/{size}/{remote_id}",
        accept="image/jpeg",
        timeout=timeout,
    )


def _content_hash_for_image(image_id: int, db_path: str) -> str | None:
    conn = None
    try:
        conn = data_connection.open_sync(db_path)
        row = conn.execute(
            "SELECT content_hash FROM images WHERE id = ?", (int(image_id),)
        ).fetchone()
    except sqlite3.Error as exc:
        raise BaseReadthroughError("Satellite content hashes are unavailable locally") from exc
    finally:
        if conn is not None:
            data_connection.close_sync(conn, db_path=db_path)
    if row is None or not isinstance(row[0], str):
        return None
    value = row[0].strip().lower()
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def _request(url: str, *, timeout: float) -> tuple[bytes, str, dict[str, str]]:
    headers = {"Accept": "multipart/mixed, application/gzip, application/json"}
    headers.update(satellite.hub_request_headers())
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
    import numpy as np  # deferred: keeps numpy off boot until a hub base preview is materialized
    from PIL import Image  # deferred: keeps Pillow off boot until a hub base preview is materialized

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


def _read_local_metadata(paths) -> dict[str, Any] | None:
    metadata_path = Path(paths.metadata)
    if not (
        Path(paths.binary).is_file()
        and metadata_path.is_file()
        and Path(paths.preview).is_file()
    ):
        return None
    try:
        return _json_metadata(metadata_path.read_bytes())
    except BaseReadthroughError:
        return None


def _materialize_from_hub(
    image_id: int,
    paths,
    *,
    db_path: str,
    source_path: str,
    timeout: float,
) -> dict[str, Any] | None:
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


def _schedule_base_warm(image_id: int, paths, *, db_path: str, source_path: str) -> None:
    key = int(image_id)
    with _warm_lock:
        if key in _warm_inflight:
            return
        _warm_inflight.add(key)

    def _run() -> None:
        try:
            _materialize_from_hub(
                key,
                paths,
                db_path=db_path,
                source_path=source_path,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            log.debug(
                "worker=develop_base_warm image_id=%s error=%s",
                key,
                exc,
            )
        finally:
            with _warm_lock:
                _warm_inflight.discard(key)

    threading.Thread(target=_run, name=f"develop-base-warm-{key}", daemon=True).start()


def fetch_base_cache_for_image(
    image_id: int,
    paths,
    *,
    db_path: str,
    source_path: str,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    blocking: bool = False,
) -> dict[str, Any] | None:
    """Fetch and atomically materialize the hub base for one local catalog row.

    Default (``blocking=False``): return any already-local base immediately and
    enqueue a hub warm on miss — Develop paint must never wait on the hub.
    Pass ``blocking=True`` for background/sync workers that need the artifact
    materialized before continuing.

    Returns ``None`` when the row has no usable content hash or a non-blocking
    miss was only scheduled. Network and artifact failures on the blocking path
    deliberately raise an honest, user-safe ``BaseReadthroughError``.
    """

    local = _read_local_metadata(paths)
    if local is not None:
        return local
    if not can_read_through():
        return None
    if not blocking:
        _schedule_base_warm(image_id, paths, db_path=db_path, source_path=source_path)
        return None
    return _materialize_from_hub(
        image_id,
        paths,
        db_path=db_path,
        source_path=source_path,
        timeout=timeout,
    )
