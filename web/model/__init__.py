"""The core: what Azimuth knows about a photo.

Four kinds of fact — what it is, where copies are, what the owner decided, and
what we computed — over five tables and seven functions. `docs/CORE.md` is the
whole design; this package is it, made real.

`connect()` installs the complete five-table schema, so an empty file is a
catalog without any migration layer or feature package running first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str = ":memory:", *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a complete catalog, new or existing.

    One constructor owns connection shape and schema installation. A fresh
    catalog is therefore a normal case, not a migration special case.
    """

    conn = sqlite3.connect(path, timeout=float(timeout))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(float(timeout) * 1000)}")
        conn.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        return conn
    except BaseException:
        conn.close()
        raise
