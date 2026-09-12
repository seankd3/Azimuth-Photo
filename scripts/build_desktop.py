#!/usr/bin/env python3
"""Freeze the one-process V2 desktop app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ENTRY = WEB / "desktop.py"
DOCUMENT = ROOT / "build" / "desktop" / "index.html"
ICON = ROOT / "desktop" / "icon.ico"


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

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_desktop_ui.py")],
        cwd=ROOT,
        check=True,
    )
    PyInstaller.__main__.run(
        [
            str(ENTRY),
            "--name=azimuth-photo",
            "--onedir",
            "--windowed",
            "--noconfirm",
            "--clean",
            f"--icon={ICON}",
            f"--paths={WEB}",
            f"--distpath={ROOT / 'dist'}",
            f"--workpath={ROOT / 'build' / 'azimuth-photo'}",
            f"--specpath={ROOT / 'build'}",
            f"--add-data={data(WEB / 'model' / 'schema.sql', 'model')}",
            f"--add-data={data(DOCUMENT, 'desktop')}",
            "--hidden-import=rawpy",
            "--hidden-import=webview.platforms.edgechromium",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
