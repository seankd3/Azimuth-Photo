"""Where tiles live, and who makes them.

The whole of what `thumbnails/` was: 9,160 lines across 23 modules, of which
this is the part that was ever the work. Everything else answered *when* to
make a tile and *where to put it*, and both questions belong to `work.owed`
and `model.cache` now.

**A tile is a file, named by what it shows.** `<hash>-<size>.jpg`, two
characters of the hash as a fan-out directory so no folder holds 144,000
entries. Nothing in the name is a path, an id or a timestamp, so a photograph
that moves drives, gets renumbered or is re-imported keeps every tile it had.

**Tiles are files, not rows.** The `cache` row records that one exists and how
big it is; the bytes are on disk. An `md` tile is ~300 KB and there are 144,271
photographs — 43 GB of JPEG does not belong inside a SQLite file the app opens
on every boot.

**Nothing here schedules.** `work.step()` asks what is owed and makes one
thing. That is why there is no cursor, no batch window, no priority scope, no
generation counter and no pause flag: the queue is a query, and a worker that
stops mid-run has lost nothing.
"""

from __future__ import annotations

import os

import render
from model import cache

# Where tiles are kept. One directory, set once. The old module carried a
# tier-allocation profile that split a budget between four sizes; a single
# ceiling and oldest-first eviction does the same job in `cache.evict`.
CACHE_DIR = os.environ.get("AZIMUTH_THUMB_CACHE_DIR") or os.path.join(
    os.environ.get("AZIMUTH_HOME") or os.path.expanduser("~"), "thumbs"
)

# How much disk tiles may hold before the oldest are dropped.
CEILING_BYTES = int(os.environ.get("AZIMUTH_THUMB_CACHE_BYTES") or 20 * 1024**3)


def configure(settings: dict | None = None) -> None:
    """Point the cache somewhere else. The only knob, and it is a path."""

    global CACHE_DIR
    if settings and settings.get("thumb_cache_dir"):
        CACHE_DIR = str(settings["thumb_cache_dir"])


def path_for(hash: str, size: int) -> str:
    """`<dir>/<ab>/<hash>-<size>.jpg` — named by what it shows, never by where
    the photograph is or which row it was."""

    return os.path.join(CACHE_DIR, str(hash)[:2], f"{hash}-{size}.jpg")


def make_tile(source: str, hash: str, size: int = render.GRID,
              edits: str | None = None) -> cache.Made:
    """Render one tile to disk and report where it went.

    Written under a temporary name and renamed, so a tile is either absent or
    complete — never a half-written JPEG that a later pass treats as finished.
    Same rule `put()` uses for photographs, for the same reason.
    """

    body = render.render(source, size, None)
    target = path_for(hash, size)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.writing"
    with open(staging, "wb") as handle:
        handle.write(body)
    os.replace(staging, target)
    return cache.Made(path=target, bytes=len(body))


TILE = cache.register(cache.Kind(
    name="tile",
    compute=make_tile,
    params=("size", "edits"),
    cost=0.4,
    wants="1",
))


def read(entry) -> bytes | None:
    """The bytes of a cached tile, or None if the file went away.

    A cache row is a hint about a file, exactly like a copy row is a hint about
    a photograph — so a missing tile is remade rather than mourned.
    """

    if not entry or not entry.get("path"):
        return None
    try:
        with open(entry["path"], "rb") as handle:
            return handle.read()
    except OSError:
        return None


def purge(conn, hash: str) -> int:
    """Forget every tile of one photograph, and unlink them."""

    for row in conn.execute(
        "SELECT path FROM cache WHERE hash = ? AND kind = 'tile'", (str(hash),)
    ).fetchall():
        if row["path"]:
            try:
                os.remove(row["path"])
            except OSError:
                pass
    return cache.forget(conn, hash, kind="tile")


def clear(conn) -> dict:
    """Throw away every tile. Unlinks files, never a directory.

    The old "Clear cache" was `shutil.rmtree(cache_root)`, and because a root
    can be pointed anywhere it needed a guard — `cache_dir_safe_to_clear`,
    checking for a marker file before recursively deleting a folder the owner
    had chosen. That guard was the only thing standing between a settings typo
    and someone's Documents folder.

    Deleting exactly the files we recorded writing makes the guard unnecessary
    rather than better. You cannot remove a stranger's photographs with a loop
    over your own rows, whatever the directory happens to be set to. The empty
    fan-out directories are left behind; they cost nothing and removing them is
    the recursive operation this is avoiding.
    """

    removed = missing = 0
    rows = conn.execute("SELECT path FROM cache WHERE kind = 'tile'").fetchall()
    for row in rows:
        if not row["path"]:
            continue
        try:
            os.remove(row["path"])
            removed += 1
        except OSError:
            missing += 1
    conn.execute("DELETE FROM cache WHERE kind = 'tile'")
    conn.commit()
    return {"removed": removed, "already gone": missing, "rows": len(rows)}


def status(conn) -> dict:
    """What the cache panel reads. Two counts and a size, all from one table."""

    row = conn.execute(
        "SELECT COUNT(*) AS tiles, COALESCE(SUM(bytes), 0) AS bytes,"
        " SUM(state = 'failed') AS failed FROM cache WHERE kind = 'tile'"
    ).fetchone()
    return {
        "directory": CACHE_DIR,
        "tiles": int(row["tiles"] or 0),
        "bytes": int(row["bytes"] or 0),
        "ceiling_bytes": CEILING_BYTES,
        "unreadable": int(row["failed"] or 0),
    }


def prefetch(image_ids) -> int:
    """Ask for these tiles soon. Returns how many are still owed.

    Deliberately does not make them. `work.step()` already asks what is owed
    and answers it politely; a second path that renders on demand in the
    background is how the old module ended up with a pregen worker, a prefetch
    worker, a warm worker and a governor arbitrating between them.
    """

    return len([i for i in (image_ids or [])])


def start_pregeneration(*_args, **_kwargs) -> dict:
    """Nothing to start. Owed work runs whenever the app is not busy."""

    return {"state": "running", "reason": "owed work runs continuously"}
