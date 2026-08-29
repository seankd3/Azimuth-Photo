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
        # SQLite has no ALTER ... IF NOT EXISTS, so a column added after
        # catalogs exist is checked for here — the one migration shape the
        # schema file cannot say. `develop` carries the photo's current
        # geometry fragment (the crop, as canonical JSON) so renditions can
        # key their recipes on it in SQL.
        held = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        if "develop" not in held:
            conn.execute("ALTER TABLE images ADD COLUMN develop TEXT")
        # `stack_of` names a stacked member's cover frame — a projection of
        # capture-time cadence (stacks.project), rebuilt whole like every
        # other decision column.
        if "stack_of" not in held:
            conn.execute("ALTER TABLE images ADD COLUMN stack_of INTEGER")
        # The member set is tiny (covers stay NULL), so the partial index
        # costs nothing and pays for every cover's member count.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stacked"
                     " ON images(stack_of) WHERE stack_of IS NOT NULL")
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
