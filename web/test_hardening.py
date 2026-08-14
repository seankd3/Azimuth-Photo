"""Regression coverage for release hardening boundaries."""

from __future__ import annotations

import db

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
from features.captions.routes import CaptionBody
from features.stacks.routes import CreateStackBody
from features.auth import passwords as share_auth
from thumbnails import generation as thumbnail_generation
import scanner
import settings


def test_scanner_skips_symlinked_media_outside_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private")
    (source / "camera.jpg").write_bytes(b"photo")
    (source / "escape.jpg").symlink_to(outside)

    rows = list(scanner.walk_images(str(source)))

    assert [row[0] for row in rows] == ["camera.jpg"]


def test_scanner_catalogs_multi_vendor_raw_extensions(tmp_path: Path):
    # The importer files ARW/NEF/ORF/RAF/RW2 into the archive; the scanner must
    # catalog them too or they become invisible custody (MASTER_PLAN 1.12).
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.arw", "b.nef", "c.orf", "d.raf", "e.rw2", "f.xyz"):
        (source / name).write_bytes(b"stub")

    rows = list(scanner.walk_images(str(source)))

    assert sorted(row[0] for row in rows) == ["a.arw", "b.nef", "c.orf", "d.raf", "e.rw2"]


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


def test_share_password_form_has_small_body_limit():
    app = FastAPI()

    @app.post("/unlock")
    async def unlock(request: Request):
        password = await share_auth.read_form_password(request)
        return {"accepted": password is not None}

    with TestClient(app) as client:
        response = client.post("/unlock", content=b"password=" + b"a" * 2048)

    assert response.json() == {"accepted": False}


def test_bulk_request_models_reject_unbounded_lists():
    oversized_cases = (
        lambda: CreateStackBody(image_ids=list(range(10_001))),
        lambda: CaptionBody(tags=["tag"] * 501),
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
