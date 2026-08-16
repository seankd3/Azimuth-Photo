"""The routes, served straight from the core.

HTTP is a transport. Nothing below decides anything — each handler is a name,
an argument or two, and one call into `library`, `render`, `rank`, `search` or
`model`. When a handler starts to reason, the reasoning belongs in the module
it is calling.

**Why this replaces `features/media/` rather than wrapping it.** The old tile
route was 592 lines and most of them were the same question asked eight ways:
is the source remote, is it cache-only, is it offline, is it missing, is the
governor holding, is there a smaller tier to stand in with, has the wave eased,
should this mark the row unavailable. Every one of those is now either a cache
lookup or `photos.state()`, and both are one line.

The five words are computed here at read time and never stored:

| | |
|---|---|
| `200` with bytes | fine |
| `204` | **preparing** — not made yet; the UI paints a smaller tile it holds |
| `409` | **away** — its drive is not attached |
| `410` | **unreadable** — the file is there and will not decode |
| `404` | **lost** — every drive is attached and none has it |

`preparing` is a `204` rather than an error on purpose: it is not a failure,
and it must never be rendered as one.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response

import library
import rank
import render
import work
from core.catalog_path import catalog_path
from data import connection
from model import cache, decisions, photos

router = APIRouter()


def db():
    """A read connection on the loop. Primary-key-shaped reads only."""

    return connection.inline_reader(catalog_path())


def _writer():
    return connection.open_sync(catalog_path())


@router.get("/api/thumb/{size}/{image_id}")
async def thumb(size: str, image_id: int):
    """One tile. Cache, or make it, or say which of the five words applies."""

    work.touched()
    longest = render.SIZES.get(size)
    if longest is None:
        return Response(status_code=404)

    conn = db()
    row = conn.execute(
        "SELECT content_hash AS hash, tail, file_size FROM images WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None:
        return Response(status_code=404)

    recipe = {"size": longest, "edits": None}
    if row["hash"]:
        entry = cache.get(conn, row["hash"], "tile", recipe)
        if entry and entry["state"] == cache.READY and entry["value"]:
            return Response(content=entry["value"], media_type="image/jpeg",
                            headers={"ETag": f'"{row["hash"]}-{longest}"',
                                     "Cache-Control": "private, max-age=31536000"})
        if entry and entry["state"] == cache.FAILED:
            return Response(status_code=410)  # unreadable, and we know why

    source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
    if source is None:
        # away or lost, and the difference is whether every drive is here.
        return Response(status_code=409 if photos.state(conn, image_id) == "away" else 404)
    if not row["hash"]:
        return Response(status_code=204)  # preparing: identity is owed first

    made = await asyncio.to_thread(_make_tile, row["hash"], longest, source)
    if made is None:
        return Response(status_code=410)
    return Response(content=made, media_type="image/jpeg",
                    headers={"ETag": f'"{row["hash"]}-{longest}"',
                             "Cache-Control": "private, max-age=31536000"})


def _make_tile(hash: str, longest: int, source: str) -> bytes | None:
    conn = _writer()
    try:
        entry = cache.make(conn, hash, "tile", source, {"size": longest, "edits": None})
        return entry["value"] if entry else None
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.get("/api/counts")
async def counts():
    return library.counts(db())


@router.get("/api/folders/tree")
async def folders():
    return {"folders": await asyncio.to_thread(library.folders, db())}


@router.get("/api/catalog")
async def catalog(folder: str | None = None, sort: str = "", starred: int = 0,
                  limit: int = 200, offset: int = 0):
    return _page(sort, limit, offset, folder, starred)


@router.get("/api/search")
async def find(q: str = "", limit: int = 200):
    import search as search_module

    return {"images": search_module.search(db(), q, limit=limit)}


@router.post("/api/image/{image_id}/rating")
async def rate(image_id: int, stars: int = 0):
    return _decide(image_id, decisions.STAR, max(0, min(5, int(stars))))


@router.post("/api/image/{image_id}/flag")
async def flag(image_id: int, flag: str = "unflagged"):
    return _decide(image_id, "flag", flag)


def _decide(image_id: int, family: str, value):
    """Write a decision, then update the index it is read through.

    The log first and the column second, always in that order: if the process
    dies between them the index is stale and `library.reindex()` repairs it,
    whereas the other order would lose the decision itself.
    """

    conn = _writer()
    try:
        row = conn.execute(
            "SELECT content_hash AS hash FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        if row is None or not row["hash"]:
            return {"ok": False, "reason": "this photo has no identity yet"}
        decisions.decide(conn, row["hash"], family, value)
        column = {"star": "stars", "flag": "flag", "status": "status"}.get(family)
        if column:
            conn.execute(
                f"UPDATE images SET {column} = ? WHERE content_hash = ?", (value, row["hash"])
            )
        conn.commit()
        return {"ok": True, family: value}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.get("/api/mosaic/next")
async def mosaic(n: int = 12, folder: str | None = None):
    """Photographs worth comparing. One query, no strategy engine.

    The route it replaces took 22 parameters — orientation, camera, lens, tag,
    people, collection, import batch, exclude-sources, a strategy name, a grid
    Elo — because narrowing the library and choosing a pair had been fused into
    one call. They are two things: `library.photos` narrows, this chooses.
    """

    return {"images": rank.candidates(db(), n=n, folder=folder)}


@router.post("/api/compare")
async def compare(winner: int, loser: int, mode: str = "mosaic"):
    """Record which of two photographs is better. The whole ranking write path.

    Nothing is propagated here and no Elo is written. A comparison is a
    decision; the ranking is recomputed from the log and the vectors, so this
    handler cannot leave a partial state behind and needs no lock, no retry and
    no undo journal.
    """

    conn = _writer()
    try:
        rows = {
            int(r["id"]): r["hash"] for r in conn.execute(
                "SELECT id, content_hash AS hash FROM images WHERE id IN (?, ?)", (winner, loser)
            )
        }
        if rows.get(winner) is None or rows.get(loser) is None:
            return {"ok": False, "reason": "both photos need an identity first"}
        decisions.decide(conn, rows[winner], decisions.COMPARE,
                         {"beat": rows[loser], "mode": mode})
        conn.commit()
        return {"ok": True, "winner": winner, "loser": loser}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.post("/api/mosaic/pick")
async def pick(winner: int, losers: str = ""):
    """One winner over several others — a mosaic click is several comparisons."""

    beaten = [int(x) for x in losers.split(",") if x.strip().isdigit()]
    for loser in beaten:
        await compare(winner, loser, mode="mosaic")
    return {"ok": True, "winner": winner, "compared": len(beaten)}


@router.post("/api/compare/undo")
async def undo_compare():
    """Take back the last comparison, by saying the opposite is not so.

    Deleting the row would be simpler and wrong: the log's job is to say what
    happened. So the last comparison is superseded, not erased, and the
    recomputed ranking simply stops counting it.
    """

    conn = _writer()
    try:
        last = conn.execute(
            "SELECT id, subject FROM decisions WHERE family = ? ORDER BY at DESC, id DESC LIMIT 1",
            (decisions.COMPARE,),
        ).fetchone()
        if last is None:
            return {"ok": False, "reason": "nothing to undo"}
        decisions.decide(conn, last["subject"], "compare_undone", {"undid": last["id"]})
        conn.commit()
        return {"ok": True, "undid": last["id"]}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


# What the UI calls a sort, and what the library calls it. The UI's names are
# the contract -- it is not being rewritten -- so the translation lives here
# rather than leaking its vocabulary into `library.SORTS`.
_UI_SORTS = {
    "date_taken": "newest", "date_taken_asc": "oldest", "newest": "newest",
    "oldest": "oldest", "taste": "best", "elo": "best", "best": "best",
    "rating": "stars", "stars": "stars", "filename": "folder", "folder": "folder",
    "added": "added", "": "newest",
}


@router.get("/api/rankings")
async def rankings(sort: str = "", limit: int = 100, offset: int = 0,
                   folder: str | None = None, min_stars: int = 0):
    """The grid's own route, in the grid's own shape.

    The counts beside the page are not decoration: the UI sizes its scroller
    from `total_images` and shows an empty grid without them. Shadowing this
    route with `{"images": [...]}` alone is exactly what "preserve the response
    shape unless the task changes the contract" is there to stop, and it showed
    up as *0 photos* over a library of 144,271.

    Two fields are honestly zero rather than omitted. Nothing is hidden for
    want of a thumbnail any more — a photo with no tile yet is *preparing*, and
    it appears in the grid while it renders.
    """

    return _page(sort, limit, offset, folder, min_stars)


def _page(sort: str, limit: int, offset: int, folder, min_stars) -> dict:
    conn = db()
    rows = library.photos(
        conn, folder=folder, sort=_UI_SORTS.get(sort or "", "newest"),
        starred=min_stars or None, limit=limit, offset=offset,
    )
    tally = library.counts(conn)
    return {
        "images": rows,
        "total_images": tally["photos"],
        "visible_images": tally["photos"],
        "total_kept": tally["photos"],
        "pending_thumbnails": 0,
        "hidden_pending_thumbnails": 0,
        "status_stale": False,
        "counts_stale": False,
    }


@router.get("/api/date-histogram")
async def date_histogram():
    return {"months": await asyncio.to_thread(library.months, db(), cover=True)}


@router.get("/api/date-groups")
async def date_groups():
    rows = await asyncio.to_thread(library.months, db())
    return {"groups": [
        {"date": r["month"], "label": library.month_label(r["month"]), "count": r["count"]}
        for r in rows
    ]}


@router.get("/api/filter-options")
async def filter_options():
    return await asyncio.to_thread(library.facets, db())


@router.get("/api/map/markers")
async def map_markers(limit: int = 5000):
    """Photographs that know where they were taken."""

    return {"markers": [
        {"id": r["id"], "lat": r["latitude"], "lon": r["longitude"]}
        for r in db().execute(
            f"SELECT i.id, i.latitude, i.longitude FROM images i"
            f" WHERE {library.IN_LIBRARY} AND i.latitude IS NOT NULL LIMIT ?",
            (int(limit),),
        )
    ]}


@router.get("/api/storage/overview")
async def storage():
    """Where the photographs live, and how much room is left.

    Read off the `drives` table rather than a configured home path, so a drive
    that came back on a different letter reports itself correctly and an
    unplugged one is simply absent instead of reporting zero bytes free.
    """

    import shutil

    from model import drives

    conn = db()
    out = []
    for row in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
        root = drives.root_of(conn, row["uuid"])
        entry = {"label": row["label"], "root": root, "attached": root is not None,
                 "is_record": bool(row["is_record"])}
        if root:
            try:
                usage = shutil.disk_usage(root)
                entry |= {"disk_total_bytes": usage.total, "disk_free_bytes": usage.free}
            except OSError:
                pass
        out.append(entry)

    first = next((d for d in out if d["attached"]), {})
    return {
        "drives": out,
        "photo_count": library.counts(conn)["photos"],
        # The old single-drive shape, so the panel keeps working.
        "home_path": first.get("root"),
        "disk_free_bytes": first.get("disk_free_bytes", 0),
        "disk_total_bytes": first.get("disk_total_bytes", 0),
        "disk_label": first.get("label", ""),
    }


@router.get("/api/image/{image_id}/state")
async def state(image_id: int):
    """One of the five words, computed now and never stored."""

    return {"state": photos.state(db(), image_id)}


@router.get("/api/work/owed")
async def owed():
    """What the machine still owes you. A status line, nothing depends on it."""

    return work.debt(db())
