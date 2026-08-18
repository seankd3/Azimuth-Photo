#!/usr/bin/env python3
"""Freeze the V2 desktop engine and only the files it can serve."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ENTRY = ROOT / "scripts" / "server_entry.py"


def data(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print(
            "PyInstaller is missing. Install web/requirements-v2.txt with this Python first.",
            file=sys.stderr,
        )
        return 2

    PyInstaller.__main__.run(
        [
            str(ENTRY),
            "--name=azimuth-server",
            "--onedir",
            "--noconfirm",
            "--clean",
            f"--paths={WEB}",
            f"--distpath={ROOT / 'dist'}",
            f"--workpath={ROOT / 'build' / 'azimuth-server'}",
            f"--specpath={ROOT / 'build'}",
            f"--add-data={data(WEB / 'model' / 'schema.sql', 'model')}",
            f"--add-data={data(WEB / 'templates' / 'v2.html', 'templates')}",
            f"--add-data={data(WEB / 'static' / 'v2', 'static/v2')}",
            "--hidden-import=rawpy",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
