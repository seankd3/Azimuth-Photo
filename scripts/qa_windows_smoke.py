#!/usr/bin/env python3
"""Fast platform-sensitive smoke checks for an Azimuth Photo Windows clone."""

from __future__ import annotations

import sys
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB_ROOT))

from core.path_groups import safe_commonpath, safe_relpath  # noqa: E402
from core.runtime_paths import resolve_runtime_paths  # noqa: E402
from features.catalog import reveal  # noqa: E402
from features.collections.suggestions import _event_title  # noqa: E402


def main() -> int:
    assert reveal.reveal_argv(r"C:\Photos\Trip", "win32")[0] == "explorer"
    assert not reveal.source_has_local_folders("hub://Family/Trip")
    assert safe_commonpath([r"C:\Photos\2024", r"D:\Photos\2024"], family="windows") is None
    assert safe_relpath(r"D:\Photos\2024\one.jpg", r"C:\Photos", family="windows") is None

    paths = resolve_runtime_paths(
        environ={
            "USERPROFILE": r"C:\Users\Azimuth",
            "LOCALAPPDATA": r"C:\Users\Azimuth\AppData\Local",
            "PHOTOARCHIVE_EXPORT_DIR": r"D:\Azimuth\Exports",
        },
        platform_name="win32",
        home=r"C:\Users\Azimuth",
    )
    assert paths.temporary_export_dir == r"D:\Azimuth\Exports"
    assert _event_title(1_704_110_400, 1_704_110_400) == "January 1, 2024"
    print("Windows smoke passed: reveal, hub source guard, dates, drive paths, export directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
