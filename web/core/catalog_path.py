"""Where the catalog is.

Twenty-eight modules used to answer this for themselves. Each kept a private
`_db_path` global, a `configure(db_path=...)` setter to fill it, a
`_configured_db_path()` reader that raised if nobody had, and a matching line
in the wiring — and every one of those wiring lines passed the same
`lambda: db.DB_PATH`. The indirection existed so tests could point at a
temporary catalog, which they already do by setting `db.DB_PATH` directly.

So it bought nothing, and cost a concept: a module could be imported but "not
configured", a state with its own error message that only ever meant someone
had forgotten a line in `wiring.py`. Worse, several modules treated it as
normal — returning quietly instead of writing to the oplog when no path had
been set.

There is one catalog. Ask for it.
"""

from __future__ import annotations

def catalog_path() -> str:
    """The absolute path of the catalog this process is working with."""

    import db

    return db.DB_PATH
