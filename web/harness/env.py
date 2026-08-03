"""The isolated home the harness runs in.

Settings and runtime paths resolve at import time, so this must run before any
app module is imported. ``python -m harness`` calls :func:`apply` first and
imports the stages afterwards.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("AZIMUTH_HARNESS_SCRATCH") or Path(tempfile.gettempdir()) / "azimuth-photo" / "harness")
HOME = SCRATCH / "home"
CORPUS = SCRATCH / "corpus"
GOLDENS = Path(__file__).resolve().parent / "goldens"

# Real camera files cannot be synthesised. Point this at a directory of them to
# record the camera-decode goldens too; without it those stages record nothing
# and the harness still runs everywhere.
REAL_CORPUS = os.environ.get("AZIMUTH_HARNESS_REAL_CORPUS")


def apply() -> None:
    """Point every runtime path at the disposable harness home."""

    HOME.mkdir(parents=True, exist_ok=True)
    CORPUS.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "HOME": str(HOME),
            "AZIMUTH_HOME": str(HOME),
            "AZIMUTH_THUMB_CACHE_DIR": str(HOME / "cache" / "previews"),
            "AZIMUTH_ORIGINALS_DIR": str(HOME / "library"),
            "AZIMUTH_MODE": "standalone",
            "AZIMUTH_ACCESS": "local",
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
