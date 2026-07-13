"""Regression coverage for release hardening boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.source_files import inspect_source_file, source_file_is_safe
from features.media import routes as media_routes
from features.publishing.routes import _attachment_name
import scanner


def test_scanner_skips_symlinked_media_outside_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private")
    (source / "camera.jpg").write_bytes(b"photo")
    (source / "escape.jpg").symlink_to(outside)

    rows = list(scanner.walk_images(str(source)))

    assert [row[0] for row in rows] == ["camera.jpg"]


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
