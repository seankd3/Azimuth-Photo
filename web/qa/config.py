"""Stable paths and fixture constants for desktop QA."""

from __future__ import annotations

import os
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_ROOT.parent

SCRATCH_ROOT = Path(
    os.environ.get("PHOTOARCHIVE_QA_SCRATCH", "/mnt/expansion/tmp/az1/qa-harness")
).resolve()
FIXTURE_HOME = SCRATCH_ROOT / "fixture-home"
CATALOG_DB = FIXTURE_HOME / "data" / "catalog" / "photoarchive.db"
PRISTINE_DB = SCRATCH_ROOT / "photoarchive.pristine.db"
MANIFEST_PATH = SCRATCH_ROOT / "fixture-manifest.json"
SERVER_LOG = SCRATCH_ROOT / "server.log"
REPORT_PATH = SCRATCH_ROOT / "report.json"
SCREENSHOT_DIR = SCRATCH_ROOT / "screenshots"

FIXTURE_VERSION = "desktop-qa-v9"
ACTIVE_IMAGE_COUNT = 4_003
VISIBLE_IMAGE_COUNT = 4_000
TRASH_IMAGE_COUNT = 6
TRASH_MIRROR_IMAGE_COUNT = 1
COLLECTION_IMAGE_COUNT = 30
DEFAULT_ACTION_TIMEOUT_MS = int(os.environ.get("PHOTOARCHIVE_QA_ACTION_TIMEOUT_MS", "30000"))


def fixture_environment() -> dict[str, str]:
    """Return the isolated server/seed environment."""

    thumb_root = FIXTURE_HOME / "cache" / "previews"
    return {
        "PHOTOARCHIVE_HOME": str(FIXTURE_HOME),
        "PHOTOARCHIVE_THUMB_CACHE_DIR": str(thumb_root),
        "PHOTOARCHIVE_SMOKE_MODE": "1",
        "PHOTOARCHIVE_MODE": "standalone",
        "PHOTOARCHIVE_ACCESS": "local",
        "TMPDIR": os.environ.get("TMPDIR", "/mnt/expansion/tmp"),
        "PYTHONPATH": str(WEB_ROOT),
    }
