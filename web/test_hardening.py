"""Regression coverage for release hardening boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.source_files import inspect_source_file, source_file_is_safe
from features.media import routes as media_routes
from features.develop import importer as develop_importer
from features.publishing.routes import _attachment_name
from features.share import auth as share_auth
from features.share import routes as share_routes
from features.sync import device_auth, hub, hub_routes
import scanner
import settings


def test_device_tokens_are_required_by_default():
    assert settings.DEFAULT_SETTINGS["require_device_token"] is True


def test_scanner_skips_symlinked_media_outside_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private")
    (source / "camera.jpg").write_bytes(b"photo")
    (source / "escape.jpg").symlink_to(outside)

    rows = list(scanner.walk_images(str(source)))

    assert [row[0] for row in rows] == ["camera.jpg"]


def test_develop_importer_does_not_restore_skipped_raw_symlinks(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.cr2"
    outside.write_bytes(b"raw")
    (source / "escape.cr2").symlink_to(outside)

    assert list(develop_importer._iter_raw_rows(str(source))) == []


def test_source_boundary_rejects_symlink_and_parent_escape(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.jpg"
    secret.write_bytes(b"secret")
    linked_directory = source / "linked"
    linked_directory.symlink_to(outside, target_is_directory=True)

    assert inspect_source_file(str(secret), str(source))[0] == "unsafe"
    assert inspect_source_file(str(linked_directory / "secret.jpg"), str(source))[0] == "unsafe"
    assert not source_file_is_safe(str(linked_directory / "secret.jpg"), str(source))


def test_media_state_rejects_existing_symlink_catalog_row(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    escaped = source / "photo.jpg"
    escaped.symlink_to(outside)
    image = {
        "missing_at": None,
        "hub_remote": 0,
        "filepath": str(escaped),
        "source_path": str(source),
        "source_online": 1,
    }

    assert asyncio.run(media_routes._source_state(image)) == "unsafe"


def test_public_download_filename_cannot_inject_response_headers():
    name = _attachment_name(7, 'portrait\r\nX-Injected: yes".jpg', suffix=".jpg")

    assert name == "portrait__X-Injected_ yes_.jpg"
    assert "\r" not in name and "\n" not in name and '"' not in name


def test_hub_upload_rejects_oversized_chunk_before_buffering(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(hub, "MAX_CHUNK_BYTES", 4)
    monkeypatch.setattr(device_auth, "require_device_token_enabled", lambda: False)
    hub_routes.configure(db_path=lambda: str(tmp_path / "unused.db"))
    app = FastAPI()
    app.include_router(hub_routes.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/sync/upload/" + "a" * 32,
            headers={"X-Offset": "0", "X-Total-Bytes": "5"},
            content=b"12345",
        )

    assert response.status_code == 413
    assert response.json() == {"error": "Upload chunk is too large"}


def test_hub_manifest_filename_rejects_cross_platform_path_forms():
    invalid = (
        "../escape.jpg",
        "/tmp/escape.jpg",
        r"..\escape.jpg",
        r"C:\escape.jpg",
        r"\\server\share\escape.jpg",
        "photo.jpg\0tail",
        "photo.jpg:stream",
        "CON.jpg",
    )

    for filename in invalid:
        try:
            hub._normalize_filename(filename)
        except ValueError:
            continue
        raise AssertionError(f"accepted unsafe filename: {filename!r}")

    assert hub._normalize_filename("summer photo-01.CR3") == "summer photo-01.CR3"


def test_share_password_form_has_small_body_limit():
    app = FastAPI()

    @app.post("/unlock")
    async def unlock(request: Request):
        password = await share_auth.read_form_password(request)
        return {"accepted": password is not None}

    with TestClient(app) as client:
        response = client.post("/unlock", content=b"password=" + b"a" * 2048)

    assert response.json() == {"accepted": False}


def test_share_unlock_failure_tracker_is_bounded():
    share_routes._unlock_failures.clear()
    for index in range(share_routes.MAX_TRACKED_UNLOCK_TOKENS + 20):
        share_routes._record_unlock_failure(f"token-{index}", now=float(index + 1))

    assert 0 < len(share_routes._unlock_failures) <= share_routes.MAX_TRACKED_UNLOCK_TOKENS
    share_routes._unlock_failures.clear()
