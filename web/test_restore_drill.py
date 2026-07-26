"""Restore drill: success, corrupted snapshot, and prod-dir refusal."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "restore_drill.py"
WEB = ROOT / "web"
VENV_PYTHON = WEB / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON if VENV_PYTHON.is_file() else sys.executable)


def _make_catalog(path: Path, *, with_develop: bool = True) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE catalog_sources (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                included INTEGER NOT NULL DEFAULT 1,
                online INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE images (
                id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES catalog_sources(id),
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                elo REAL DEFAULT 1200.0,
                status TEXT DEFAULT 'kept',
                date_taken TEXT DEFAULT NULL,
                missing_at REAL DEFAULT NULL
            );
            CREATE TABLE develop_settings (
                image_id INTEGER PRIMARY KEY,
                settings TEXT NOT NULL DEFAULT '{}',
                origin TEXT NOT NULL DEFAULT 'user',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO catalog_sources (id, path, display_name, online) VALUES (1, '/tmp', 'tmp', 1)"
        )
        for index in range(1, 6):
            filepath = f"/tmp/fixture/img{index}.dng"
            conn.execute(
                "INSERT INTO images (id, source_id, filename, filepath, elo, date_taken) "
                "VALUES (?, 1, ?, ?, 1200.0, '2024-01-0' || ?)",
                (index, f"img{index}.dng", filepath, index),
            )
            if with_develop:
                conn.execute(
                    "INSERT INTO develop_settings (image_id, settings) VALUES (?, ?)",
                    (index, json.dumps({"Exposure2012": 0.1 * index, "Version": 17})),
                )
        conn.commit()
    finally:
        conn.close()
    return path


def _seal_fixture_snapshot(catalog: Path, backup_root: Path) -> Path:
    """Build a sealed .db.gz the same way production publishes (gzip of a sealed db)."""
    backup_root.mkdir(parents=True, exist_ok=True)
    name = "azimuth-20260719-120000.db.gz"
    dest = backup_root / name
    with open(catalog, "rb") as raw, gzip.open(dest, "wb", compresslevel=6) as gz:
        gz.write(raw.read())
    return dest


def _run_drill(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


@pytest.fixture()
def fixture_backup(tmp_path: Path) -> tuple[Path, Path]:
    catalog = _make_catalog(tmp_path / "azimuth.db")
    backup_root = tmp_path / "backups"
    snapshot = _seal_fixture_snapshot(catalog, backup_root)
    return backup_root, snapshot


def test_restore_drill_success_exit_zero(fixture_backup: tuple[Path, Path], tmp_path: Path) -> None:
    backup_root, snapshot = fixture_backup
    result = _run_drill(
        [
            "--backup-root",
            str(backup_root),
            "--snapshot",
            str(snapshot),
            "--scratch-parent",
            str(tmp_path / "scratch-parent"),
            "--spot-rows",
            "3",
            "--seed",
            "7",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert "ok snapshot=" in result.stdout
    assert "images=5" in result.stdout
    assert "spot_checked=3" in result.stdout
    scratch_parent = tmp_path / "scratch-parent"
    if scratch_parent.exists():
        leftovers = list(scratch_parent.glob("azimuth-restore-drill-*"))
        assert leftovers == [], leftovers


def test_restore_drill_corrupted_snapshot_fails_loudly(
    fixture_backup: tuple[Path, Path], tmp_path: Path
) -> None:
    backup_root, snapshot = fixture_backup
    snapshot.write_bytes(b"not-a-gzip-payload-at-all")
    result = _run_drill(
        [
            "--backup-root",
            str(backup_root),
            "--snapshot",
            str(snapshot),
            "--scratch-parent",
            str(tmp_path / "scratch-parent"),
        ]
    )
    assert result.returncode == 1, result.stdout
    assert result.stderr.strip(), "corrupt path must fail loudly on stderr"
    assert "Failed to decompress" in result.stderr or "corrupt" in result.stderr.lower()


def test_restore_drill_prod_dir_refusal_preserves_target(
    fixture_backup: tuple[Path, Path], tmp_path: Path
) -> None:
    backup_root, snapshot = fixture_backup

    marked = tmp_path / "looks-like-backup-root"
    marked.mkdir()
    (marked / ".azimuth-backup-owner").write_text(
        json.dumps({"catalog_path": "/prod/azimuth.db"}),
        encoding="utf-8",
    )
    marker_before = (marked / ".azimuth-backup-owner").read_text(encoding="utf-8")
    result = _run_drill(
        [
            "--backup-root",
            str(backup_root),
            "--snapshot",
            str(snapshot),
            "--scratch",
            str(marked),
        ]
    )
    assert result.returncode == 2, result.stderr
    assert "Refusing scratch" in result.stderr
    assert ".azimuth-backup-owner" in result.stderr
    assert marked.is_dir()
    assert (marked / ".azimuth-backup-owner").read_text(encoding="utf-8") == marker_before

    live = tmp_path / "looks-like-catalog"
    live.mkdir()
    (live / "azimuth.db").write_bytes(b"live")
    result_live = _run_drill(
        [
            "--backup-root",
            str(backup_root),
            "--snapshot",
            str(snapshot),
            "--scratch",
            str(live),
        ]
    )
    assert result_live.returncode == 2, result_live.stderr
    assert "azimuth.db" in result_live.stderr
    assert (live / "azimuth.db").read_bytes() == b"live"


def test_restore_drill_picks_newest_sealed(tmp_path: Path) -> None:
    catalog = _make_catalog(tmp_path / "azimuth.db")
    backup_root = tmp_path / "backups"
    older = backup_root / "azimuth-20260710-040000.db.gz"
    newer = backup_root / "azimuth-20260718-040000.db.gz"
    backup_root.mkdir(parents=True, exist_ok=True)
    payload = catalog.read_bytes()
    for path in (older, newer):
        with gzip.open(path, "wb") as gz:
            gz.write(payload)
    older.write_bytes(b"corrupt-older")
    result = _run_drill(
        [
            "--backup-root",
            str(backup_root),
            "--scratch-parent",
            str(tmp_path / "scratch-parent"),
            "--spot-rows",
            "1",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert "azimuth-20260718-040000.db.gz" in result.stdout
