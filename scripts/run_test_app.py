#!/usr/bin/env python3
"""Run the app against the small SSD test archive.

Everything lives under one disposable directory, so this never touches the real
catalog, the real caches, or the hub. Delete `C:\\Azimuth Test` and the whole
environment is gone.

    python scripts/run_test_app.py            # serve on http://127.0.0.1:8011/d
    python scripts/run_test_app.py --fresh    # discard the test catalog first

The archive itself is built by scripts/make_test_library.py.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
HOME = Path(r"C:\Azimuth Test")
LIBRARY = HOME / "Photos"
PORT = 8011


def environment() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "AZIMUTH_HOME": str(HOME),
        "AZIMUTH_MODE": "standalone",
        "AZIMUTH_ACCESS": "local",
        "PYTHONPATH": str(WEB),
    })
    return env


async def register_library(db_path: str) -> None:
    sys.path.insert(0, str(WEB))
    from data.repositories import catalog as catalog_repository

    for root in ("Edits", "Raws", "Snapshots"):
        directory = LIBRARY / root
        if directory.is_dir():
            source = await catalog_repository.add_or_restore_source(db_path, str(directory))
            print(f"  source {source['id']}: {directory}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="discard the test catalog first")
    args = parser.parse_args()

    if not LIBRARY.is_dir():
        print("no test archive yet — run scripts/make_test_library.py first", file=sys.stderr)
        return 1

    data_dir = HOME / "data"
    if args.fresh and data_dir.exists():
        shutil.rmtree(data_dir)
        print(f"removed {data_dir}")

    env = environment()
    os.environ.update(env)
    sys.path.insert(0, str(WEB))
    import db as app_db

    print(f"catalog: {app_db.DB_PATH}")
    Path(app_db.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(app_db.init_db())
    asyncio.run(register_library(app_db.DB_PATH))

    print(f"\nserving http://127.0.0.1:{PORT}/d   (Ctrl-C to stop)")
    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(WEB),
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
