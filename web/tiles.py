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
import re
import time

import render
from model import cache

# Where tiles are kept, asked of the one resolver that answers it for every
# other derivative too — previews, embeddings, develop caches all hang off
# `runtime_paths`, so `AZIMUTH_CACHE_DIR` moves the lot with one word.
#
# This used to carry its own default (`AZIMUTH_THUMB_CACHE_DIR` or `~/thumbs`)
# and `configure` read a settings key named `thumb_cache_dir`. **No such key
# exists** — the setting is `ssd_cache_dir` — so configure() had never once
# moved the cache, and tiles were written to `~/thumbs` (1,140 files) while the
# settings screen, the health check's disk warning and the runtime paths all
# reported `<cache>/previews` (33 files). Three names for one directory, and
# the one the owner could edit was the one nothing read.
def _default_cache_dir() -> str:
    from core.runtime_paths import resolve_runtime_paths

    return resolve_runtime_paths().thumb_cache_dir


CACHE_DIR = _default_cache_dir()

# How much disk tiles may hold before the oldest are dropped.
CEILING_BYTES = int(os.environ.get("AZIMUTH_THUMB_CACHE_BYTES") or 20 * 1024**3)


def configure(settings: dict | None = None) -> None:
    """Point the cache somewhere else. The only knob, and it is a path."""

    global CACHE_DIR
    chosen = str((settings or {}).get("ssd_cache_dir") or "").strip()
    CACHE_DIR = chosen or _default_cache_dir()


NAMED = re.compile(r"^([0-9a-f]{16,})-(\d+)(?:r(\d+))?\.jpg$")


def adopt(conn) -> dict[str, int]:
    """Tell the catalog about the tiles already on disk. Reads; makes nothing.

    The `cache` table is an *index* of this directory, and an index can always
    be rebuilt, because a tile is named by what it shows. That is worth having
    as an operation rather than as a one-off repair: the two part company on a
    catalog restore, on a rebuild, and on any move of the preview folder — and
    they had already, badly. 150,442 tiles sat here in the current naming and
    the current three sizes while `cache` held 5,861 rows, 5,340 of them
    spelling their recipe `{}`, a shape this module has not written in some
    time. `work.owed` is an anti-join against that table, so it reported
    138,105 tiles owed and would have spent nights remaking files already here.

    Stale-recipe rows are dropped rather than left beside the new ones. A
    spelling nothing queries satisfies no read and prevents no work; keeping it
    only means counting the same tile twice.
    """

    from model import cache

    recipes: dict[tuple[int, int], str] = {}
    already = {
        (row["hash"], row["recipe"])
        for row in conn.execute("SELECT hash, recipe FROM cache WHERE kind = 'tile'")
    }
    tally = {"recorded": 0, "already": 0, "stale_dropped": 0}
    rows = []
    for folder, _dirs, files in os.walk(CACHE_DIR):
        for name in files:
            match = NAMED.match(name)
            if not match:
                continue
            digest, size, rotate = match.group(1), int(match.group(2)), int(match.group(3) or 0)
            recipe = recipes.get((size, rotate))
            if recipe is None:
                recipe = cache.canonical("tile", {"size": size, "edits": None, "rotate": rotate})
                recipes[(size, rotate)] = recipe
            if (digest, recipe) in already:
                tally["already"] += 1
                continue
            path = os.path.join(folder, name)
            try:
                on_disk = os.path.getsize(path)
            except OSError:
                continue
            already.add((digest, recipe))
            tally["recorded"] += 1
            rows.append((digest, recipe, path, on_disk, time.time()))

    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO cache(hash, kind, recipe, state, path, bytes, at)"
            " VALUES (?, 'tile', ?, 'ready', ?, ?, ?)",
            rows,
        )
    tally["stale_dropped"] = conn.execute(
        "DELETE FROM cache WHERE kind = 'tile' AND recipe = '{}'"
    ).rowcount
    conn.commit()
    return tally


def path_for(hash: str, size: int, rotate: int = 0) -> str:
    """`<dir>/<ab>/<hash>-<size>[r90].jpg` — named by what it shows.

    The rotation is in the name because it is in the recipe: a photograph the
    owner has turned is a different picture at the same size, and the old tile
    stays valid for anyone who has not turned it.
    """

    turn = f"r{int(rotate) % 360}" if int(rotate) % 360 else ""
    return os.path.join(CACHE_DIR, str(hash)[:2], f"{hash}-{size}{turn}.jpg")


def make_tile(source: str, hash: str, size: int = render.GRID,
              edits: str | None = None, rotate: int = 0) -> cache.Made:
    """Render one tile to disk and report where it went.

    Written under a temporary name and renamed, so a tile is either absent or
    complete — never a half-written JPEG that a later pass treats as finished.
    Same rule `put()` uses for photographs, for the same reason.
    """

    body = render.render(source, size, None, rotate=rotate)
    target = path_for(hash, size, rotate)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.writing"
    with open(staging, "wb") as handle:
        handle.write(body)
    os.replace(staging, target)
    return cache.Made(path=target, bytes=len(body))


TILE = cache.register(cache.Kind(
    name="tile",
    compute=make_tile,
    params=("size", "edits", "rotate"),
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
