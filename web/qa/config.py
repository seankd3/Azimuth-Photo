"""Stable paths and fixture constants for desktop QA."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_ROOT.parent

DEFAULT_SCRATCH_ROOT = Path(tempfile.gettempdir()) / "azimuth-photo" / "qa-harness"
SCRATCH_ROOT = Path(os.environ.get("PHOTOARCHIVE_QA_SCRATCH", DEFAULT_SCRATCH_ROOT)).resolve()
FIXTURE_HOME = SCRATCH_ROOT / "fixture-home"
CATALOG_DB = FIXTURE_HOME / "data" / "catalog" / "photoarchive.db"
PRISTINE_DB = SCRATCH_ROOT / "photoarchive.pristine.db"
MANIFEST_PATH = SCRATCH_ROOT / "fixture-manifest.json"
REPORT_PATH = SCRATCH_ROOT / "report.json"
RUNS_ROOT = SCRATCH_ROOT / "runs"
SCREENSHOT_DIR = SCRATCH_ROOT / "screenshots"

ACTIVE_IMAGE_COUNT = int(os.environ.get("PHOTOARCHIVE_QA_ACTIVE_IMAGE_COUNT", "4003"))
if ACTIVE_IMAGE_COUNT < 4_003:
    raise ValueError("PHOTOARCHIVE_QA_ACTIVE_IMAGE_COUNT must be at least 4003")

VISIBLE_IMAGE_COUNT = ACTIVE_IMAGE_COUNT - 4
FIXTURE_VERSION = f"desktop-qa-v21-{ACTIVE_IMAGE_COUNT}"
TRASH_IMAGE_COUNT = 6
TRASH_MIRROR_IMAGE_COUNT = 1
COLLECTION_IMAGE_COUNT = 30

def _wait_multiplier() -> float:
    value = float(os.environ.get("QA_WAIT_MULTIPLIER", "1"))
    if value <= 0:
        raise ValueError("QA_WAIT_MULTIPLIER must be greater than zero")
    return value


WAIT_MULTIPLIER = _wait_multiplier()


def scaled_seconds(seconds: float) -> float:
    """Apply the one QA load-tolerance knob to condition deadlines."""

    return seconds * WAIT_MULTIPLIER


def scaled_timeout_ms(milliseconds: int) -> int:
    return int(milliseconds * WAIT_MULTIPLIER)


DEFAULT_ACTION_TIMEOUT_MS = scaled_timeout_ms(int(os.environ.get("PHOTOARCHIVE_QA_ACTION_TIMEOUT_MS", "30000")))


def fixture_environment() -> dict[str, str]:
    """Return the isolated server/seed environment."""

    thumb_root = FIXTURE_HOME / "cache" / "previews"
    return {
        # The legacy import route derives its default destination from HOME.
        # Keep that path inside the disposable fixture too.
        "HOME": str(FIXTURE_HOME),
        "PHOTOARCHIVE_HOME": str(FIXTURE_HOME),
        "PHOTOARCHIVE_THUMB_CACHE_DIR": str(thumb_root),
        # Staged-import tests must never resolve the operator's real library.
        "PHOTOARCHIVE_ORIGINALS_DIR": str(FIXTURE_HOME / "import-library"),
        "PHOTOARCHIVE_SMOKE_MODE": "1",
        "PHOTOARCHIVE_MODE": "standalone",
        "PHOTOARCHIVE_ACCESS": "local",
        # The seeding subprocess may have a different TMPDIR. Keep all fixture
        # paths on this invocation's chosen QA scratch root.
        "PHOTOARCHIVE_QA_SCRATCH": str(SCRATCH_ROOT),
        "TMPDIR": os.environ.get("TMPDIR", "/mnt/expansion/tmp"),
        "PYTHONPATH": str(WEB_ROOT),
    }
