#!/usr/bin/env python3
"""Azimuth Photo server entrypoint for frozen binaries and source/Docker runs.

Honors all existing PHOTOARCHIVE_* environment variables. With none set it
uses platform-default data dirs (see web/core/runtime_paths.py) and serves
on 127.0.0.1:8000. Docker/compose should set PHOTOARCHIVE_HOST=0.0.0.0 and
PHOTOARCHIVE_HOME=/data.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def bundle_root() -> Path:
    """Return the directory that contains the web package layout."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def ensure_sys_path() -> Path:
    """Make `import app` work for source, Docker, and frozen layouts."""
    root = bundle_root()
    if getattr(sys, "frozen", False):
        # PyInstaller onedir places modules + datas under _MEIPASS.
        return root
    web = root / "web"
    web_str = str(web)
    if web_str not in sys.path:
        sys.path.insert(0, web_str)
    return web


def _default_host() -> str:
    return os.environ.get("PHOTOARCHIVE_HOST") or "127.0.0.1"


def _default_port() -> int:
    raw = os.environ.get("PHOTOARCHIVE_PORT") or "8000"
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid PHOTOARCHIVE_PORT={raw!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photoarchive-server",
        description="Azimuth Photo library server (hub / standalone / satellite).",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: PHOTOARCHIVE_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: PHOTOARCHIVE_PORT or 8000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = args.host or _default_host()
    port = args.port if args.port is not None else _default_port()

    # Keep env and CLI aligned so runtime_paths / status probes see the same values.
    os.environ.setdefault("PHOTOARCHIVE_HOST", host)
    os.environ.setdefault("PHOTOARCHIVE_PORT", str(port))

    ensure_sys_path()

    import uvicorn

    # Import after sys.path + env defaults so apply_environment_defaults() sees them.
    from app import app  # noqa: WPS433 — intentional late import

    # Single process: the catalog is SQLite. Do not raise workers here.
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.environ.get("PHOTOARCHIVE_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
