#!/usr/bin/env python3
"""Start the private V2 engine used by the desktop app.

Azimuth is one person's app on one machine, so it binds loopback and only
loopback. That is not a default -- there is no flag, no environment variable
and no argument that can make it listen outward, which is what allows the
whole authentication layer to not exist. Anything reachable from another
machine would need it back.
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
    """Make the V2 web modules importable from source and frozen layouts."""
    root = bundle_root()
    if getattr(sys, "frozen", False):
        # PyInstaller onedir places modules + datas under _MEIPASS.
        return root
    web = root / "web"
    web_str = str(web)
    if web_str not in sys.path:
        sys.path.insert(0, web_str)
    return web


HOST = "127.0.0.1"


def _default_port() -> int:
    raw = os.environ.get("AZIMUTH_PORT") or "8000"
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid AZIMUTH_PORT={raw!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="azimuth-server",
        description="Azimuth Photo library server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: AZIMUTH_PORT or 8000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = HOST
    port = args.port if args.port is not None else _default_port()

    # Keep env and CLI aligned so runtime_paths / status probes see the same values.
    os.environ.setdefault("AZIMUTH_HOST", host)
    os.environ.setdefault("AZIMUTH_PORT", str(port))

    ensure_sys_path()

    import uvicorn

    # Import after sys.path and environment defaults select the private catalog.
    from v2_app import app  # noqa: WPS433 — intentional late import

    # Single process: the catalog is SQLite. Do not raise workers here.
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.environ.get("AZIMUTH_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
