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
        # SQLite has no ALTER ... IF NOT EXISTS, so a column the schema
        # gained after catalogs existed is added here first — the one
        # migration shape the schema file cannot say — and the schema's own
        # indexes over those columns then install like any other.
        held = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        for column, kind in (("develop", "TEXT"), ("stack_of", "INTEGER")):
            if held and column not in held:
                conn.execute(f"ALTER TABLE images ADD COLUMN {column} {kind}")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        # Without sqlite_stat1 the planner guesses, and at catalog scale it
        # guesses a partial-index scan with a row fetch per entry — measured
        # 587 ms against 42 ms for the plan it picks once it has statistics.
        # `optimize` re-analyzes only what changed, so this is a no-op on
        # every open after the first.
        conn.execute("PRAGMA optimize")
        return conn
    except BaseException:
        conn.close()
        raise
