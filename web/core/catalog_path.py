"""Where the catalog is.

Twenty-eight modules used to answer this for themselves. Each kept a private
`_db_path` global, a `configure(db_path=...)` setter to fill it, a
`_configured_db_path()` reader that raised if nobody had, and a matching line
in the wiring — and every one of those wiring lines passed the same
`lambda: db.DB_PATH`. The indirection existed so tests could point at a
temporary catalog, which they already do by setting the path directly.

So it bought nothing, and cost a concept: a module could be imported but "not
configured", a state with its own error message that only ever meant someone
had forgotten a line in `wiring.py`. Worse, several modules treated it as
normal — returning quietly instead of writing to the oplog when no path had
been set.

There is one catalog. Ask for it.

**And this module is where it lives.** It used to return `db.DB_PATH`, which
made the core's answer to "where is the catalog" a forwarder into the old data
layer — so 204 call sites that wanted nothing but a path had to import a
1,077-line module of repositories to get one, and `db.py` stayed load-bearing
because of it. The value is a path; a path has no business inside a data layer.
"""

from __future__ import annotations

_path: str | None = None


def catalog_path() -> str:
    """The absolute path of the catalog this process is working with.

    Resolved on first ask rather than at import, so a process that never opens
    a catalog never computes one, and a test that points somewhere else is
    never racing an import.
    """

    global _path
    if _path is None:
        from core.runtime_paths import resolve_runtime_paths

        _path = resolve_runtime_paths().catalog_db
    return _path


def use(path: str) -> str:
    """Work with this catalog instead. Returns the previous one, to restore."""

    global _path
    previous = catalog_path()
    _path = str(path)
    return previous
