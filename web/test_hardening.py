"""Regression coverage for release hardening boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from core.source_files import inspect_source_file, source_file_is_safe
from features.media import routes as media_routes
from features.settings import routes as settings_routes
from features.develop import importer as develop_importer
from features.publishing.routes import _attachment_name
from features.captions.routes import CaptionBody
from features.stacks.routes import CreateStackBody
from features.share import auth as share_auth
from features.share import routes as share_routes
from features.sync import device_auth, hub, hub_routes
from features.sync.oplog_routes import OplogEntry, OplogPushRequest
from thumbnails import generation as thumbnail_generation
import scanner
import settings


def test_device_token_enforcement_defaults_off_until_pairing_onboarded():
    # Opt-in for now so existing unpaired satellites keep syncing; the security
    # property that MATTERS (enforcement when enabled) is covered separately.
    assert settings.DEFAULT_SETTINGS["require_device_token"] is False


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


def test_capacity_pressure_never_evicts_a_live_share_lockout():
    share_routes._unlock_failures.clear()
    for _ in range(share_routes.UNLOCK_FAILURE_LIMIT):
        share_routes._record_unlock_failure("under-attack", now=1.0)
    assert share_routes._unlock_retry_after("under-attack", now=2.0) is not None

    for index in range(share_routes.MAX_TRACKED_UNLOCK_TOKENS + 50):
        share_routes._record_unlock_failure(f"flood-{index}", now=2.0)

    assert share_routes._unlock_retry_after("under-attack", now=3.0) is not None
    assert len(share_routes._unlock_failures) <= share_routes.MAX_TRACKED_UNLOCK_TOKENS
    share_routes._unlock_failures.clear()


def test_bulk_request_models_reject_unbounded_lists():
    oversized_cases = (
        lambda: CreateStackBody(image_ids=list(range(10_001))),
        lambda: CaptionBody(tags=["tag"] * 501),
        lambda: OplogPushRequest(
            entries=[
                OplogEntry(
                    origin="device",
                    origin_seq=index + 1,
                    content_hash="a" * 32,
                    family="flag",
                    payload={},
                    ts=1.0,
                )
                for index in range(5001)
            ]
        ),
    )

    for build in oversized_cases:
        try:
            build()
        except ValidationError:
            continue
        raise AssertionError("accepted an oversized bulk request")


def test_media_warm_normalization_caps_ids_before_database_lookup():
    requested, all_ids = media_routes._normalize_warm_requests(
        {"sm": list(range(1, 10_001))}
    )

    assert len(requested["sm"]) == 96
    assert len(all_ids) == 96


def test_batch_flag_request_limit_is_bounded():
    assert settings_routes._batch_image_ids_too_large(
        [1] * (settings_routes.MAX_BATCH_IMAGE_IDS + 1)
    )


def test_malformed_media_failure_does_not_poison_next_thumbnail_job(tmp_path: Path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8truncated")
    valid = tmp_path / "valid.jpg"
    Image.new("RGB", (8, 6), (12, 34, 56)).save(valid, "JPEG")
    retry_after = {}

    def generate(path: Path, image_id: int):
        return thumbnail_generation.generate_missing_thumbnails(
            str(path),
            "sm",
            image_id,
            include_smaller_tiers=False,
            hot=False,
            allow_stale_fallback=False,
            planned_thumbnail_sizes=lambda *_args, **_kwargs: ["sm"],
            sizes={"sm": 64},
            load_source_image=lambda filepath, *_args, **_kwargs: Image.open(filepath).copy(),
            queue_orientation=lambda *_args: None,
            resize_to_long_side=lambda image, _size: image.copy(),
            build_source_signature=lambda *_args: "signature",
            encode_and_cache_thumbnail=lambda *_args, **_kwargs: (_args[3], b"jpeg", True),
            mark_source_missing_from_error=lambda *_args: False,
            thumbnail_retry_after=retry_after,
            thumbnail_retry_seconds=60,
            log=lambda _message: None,
        )

    assert generate(broken, 1) is None
    assert retry_after
    assert generate(valid, 2) == b"jpeg"


def test_truncated_raw_preview_is_contained(tmp_path: Path):
    broken_raw = tmp_path / "broken.dng"
    broken_raw.write_bytes(b"II*\x00\x08\x00\x00\x00truncated")

    assert thumbnail_generation.load_raw_preview(str(broken_raw), 256) is None
