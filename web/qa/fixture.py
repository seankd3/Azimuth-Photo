"""Fast reusable lifecycle for the deterministic QA library."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from qa.config import (
    CATALOG_DB,
    FIXTURE_HOME,
    FIXTURE_VERSION,
    MANIFEST_PATH,
    PRISTINE_DB,
    SCRATCH_ROOT,
    fixture_environment,
)


def _current_manifest() -> dict | None:
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if data.get("version") == FIXTURE_VERSION else None


def ensure_fixture() -> tuple[dict, bool]:
    """Build once per fixture version, then return its manifest."""

    manifest = _current_manifest()
    if manifest and PRISTINE_DB.is_file() and CATALOG_DB.is_file():
        return manifest, False

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    if FIXTURE_HOME.exists():
        shutil.rmtree(FIXTURE_HOME)
    for path in (PRISTINE_DB, MANIFEST_PATH):
        path.unlink(missing_ok=True)

    env = os.environ.copy()
    env.update(fixture_environment())
    subprocess.run(
        [sys.executable, "-m", "qa.seed_worker"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        check=True,
    )
    manifest = _current_manifest()
    if manifest is None:
        raise RuntimeError("QA fixture worker completed without a valid manifest")
    return manifest, True


def reset_fixture() -> dict:
    """Restore the active catalog and destructive trash files before a run."""

    manifest, _ = ensure_fixture()
    CATALOG_DB.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(f"{CATALOG_DB}{suffix}").unlink(missing_ok=True)
    shutil.copy2(PRISTINE_DB, CATALOG_DB)

    primary = Path(manifest["primary_source"])
    trash_root = primary / ".trash" / "Trash candidates"
    if trash_root.parent.exists():
        shutil.rmtree(trash_root.parent)
    shared = FIXTURE_HOME / "cache" / "previews" / "qa-shared-preview.jpg"
    trash_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, int(manifest["trash_images"]) + 1):
        shutil.copy2(shared, trash_root / f"qa-trash-{index}.jpg")
    return manifest
