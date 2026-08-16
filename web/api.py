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
import tiles
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
        body = tiles.read(entry) if entry and entry["state"] == cache.READY else None
        if body:
            return Response(content=body, media_type="image/jpeg",
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
        return tiles.read(entry) if entry else None
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.get("/api/counts")
async def counts():
    return library.counts(db())


@router.get("/api/folders/tree")
async def folders():
    """The folder tree, merged across every drive.

    Returned under one source rather than one per drive, and that is the
    design rather than a shim: a folder is a prefix of a tail, so the same
    shoot filed on the working disk and the archive is *one* node that happens
    to be held twice. Lightroom shows it twice and makes the owner know which
    copy they clicked.

    `drives` rides along on each node as a quiet fact — where these
    photographs are — instead of being the axis the tree is built on.
    """

    tree = await asyncio.to_thread(library.folder_tree, db())
    return {
        "sources": [{
            "id": 1,
            "path": "",
            "display_name": "Library",
            "online": True,
            "folders": tree,
        }],
        "folders": tree,
    }


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


@router.get("/api/similar/{image_id}")
async def similar(image_id: int, limit: int = 50):
    """Photographs that look like this one.

    The same embedding space that carries propagated taste. It is one lookup
    and one matrix multiply, so "find similar" and "spread a judgement to
    look-alikes" are the same operation seen from two ends — which is why
    neither needed its own index, its own cache or its own worker.
    """

    import search as search_module

    conn = db()
    row = conn.execute(
        "SELECT content_hash AS hash FROM images WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None:
        return Response(status_code=404)

    found = []
    if row["hash"]:
        entry = cache.get(conn, row["hash"], search_module.EMBEDDING)
        if entry and entry["value"]:
            ids = await asyncio.to_thread(
                search_module._semantic, conn, search_module._vector(entry["value"]),
                max(1, min(int(limit), 500)) + 1,
            )
            found = [i for i in ids if i != image_id]

    return {"images": _rows_for(conn, found), "source_id": image_id}


@router.get("/api/duplicates")
async def duplicates(limit: int = 200):
    """Photographs whose bytes are the same.

    The hash names candidates and nothing more — never permission to delete and
    never permission to merge. So this reports, and any acting on it is the
    owner's.
    """

    conn = db()
    groups = [
        {"hash": row["content_hash"], "count": row["n"],
         "ids": [int(x) for x in str(row["ids"]).split(",")]}
        for row in conn.execute(
            f"""
            SELECT content_hash, COUNT(*) AS n, GROUP_CONCAT(id) AS ids
            FROM images i WHERE {library.IN_LIBRARY} AND content_hash IS NOT NULL
            GROUP BY content_hash HAVING n > 1 ORDER BY n DESC LIMIT ?
            """,
            (int(limit),),
        )
    ]
    return {"groups": groups, "total": len(groups)}


@router.get("/api/image/{image_id}/exif")
async def exif(image_id: int):
    """What the camera recorded, as the catalog already holds it.

    Read from the row, not from the file. The old route opened the original —
    possibly a 45 MP RAW on a sleeping archive drive — and kept a per-process
    dictionary to avoid doing it twice. Every field it returned was scanned
    into the catalog at import, so the disk read bought nothing and the cache
    existed to hide it.
    """

    row = db().execute(
        "SELECT id, tail, date_taken, camera_make, camera_model, lens, file_size,"
        " width, height, orientation, latitude, longitude, file_ext"
        " FROM images WHERE id = ?", (image_id,)
    ).fetchone()
    if row is None:
        return Response(status_code=404)
    out = dict(row)
    out["camera"] = " ".join(x for x in (out.pop("camera_make"), out.pop("camera_model")) if x).strip()
    out["filename"] = (out["tail"] or "").split("/")[-1]
    return out


def _rows_for(conn, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    holes = ",".join("?" for _ in ids)
    found = {
        row["id"]: dict(row) for row in conn.execute(
            f"SELECT id, tail, date_taken, stars, elo, content_hash AS hash, width, height"
            f" FROM images WHERE id IN ({holes})", ids
        )
    }
    return [found[i] for i in ids if i in found]


# Faces are a cache kind this machine cannot make: insightface and onnxruntime
# are not installed, `people` and `face_detections` hold zero rows, and the
# 93,220-row backlog that fed them was the largest queue in the catalog. In the
# core's terms that is not a subsystem, it is owed work with `here()` false --
# which records nothing rather than a failure, so the machine that *can* do it
# is not looking at rows saying these photographs have no faces.
_NO_FACES = {
    "capability": {
        "key": "people", "label": "People recognition", "available": False,
        "missing": ["insightface", "onnxruntime"], "optional_missing": [],
        "requirements_file": "requirements-ai-people.txt",
        "install_command": "python -m pip install -r requirements-ai-people.txt",
        "runtime_install": False,
        "message": "People recognition is not installed. Existing library data "
                   "remains available; install the optional pack to create new results.",
    },
    "active": False,
}


@router.get("/api/people")
async def people():
    return {
        "sections": {"most_seen": [], "named_people": [], "needs_review": [], "other_faces": []},
        "counts": {"people": 0, "named_people": 0, "unknown_people": 0, "other_faces": 0,
                   "detected_faces": 0, "pending_cached_images": 0,
                   "merge_suggestions": 0, "scan": {}},
        "ranking_policy": "distinct_photo_count_first",
        "identity_policy": "face_embeddings_only",
        "source_files_preserved": True,
        "status": _NO_FACES,
    }


@router.get("/api/people/status")
async def people_status():
    return _NO_FACES


# A caption the owner writes is a decision. A caption the machine writes is a
# cache kind. CORE.md puts it exactly: "your answer about the machine's answer
# is a decision stored elsewhere". The old surface was 755 lines and one table
# holding zero rows, because it kept both in the same place and needed a
# scan ledger, an FTS shadow and a status worker to tell them apart.
CAPTION = "caption"


@router.get("/api/captions/status")
async def captions_status():
    return {
        "capability": {
            "key": "captions", "label": "Captions", "available": False,
            "missing": ["captioning model"],
            "requirements_file": "requirements-ai-captions.txt",
            "install_command": "python -m pip install -r requirements-ai-captions.txt",
            "message": "Captioning is not installed. Captions you write yourself are "
                       "kept regardless; install the optional pack to generate new ones.",
        },
        "active": False,
    }


@router.get("/api/image/{image_id}/caption")
async def caption(image_id: int):
    conn = db()
    row = conn.execute(
        "SELECT content_hash AS hash FROM images WHERE id = ?", (image_id,)
    ).fetchone()
    written = decisions.latest(conn, row["hash"], CAPTION) if row and row["hash"] else None
    if not written:
        return {"image_id": image_id, "caption": "", "tags": [], "quality": "",
                "user_edited": False, "has_caption": False}
    return {"image_id": image_id, "caption": written.get("caption", ""),
            "tags": written.get("tags", []), "quality": "",
            "user_edited": True, "has_caption": True}


# A caption and its tags are bounded here rather than by a request model. The
# model this replaced existed to reject unbounded lists, and dropping it would
# have quietly removed that guard -- caught by a hardening test that named the
# model directly.
MAX_TAGS = 500
MAX_CAPTION = 4000


@router.post("/api/image/{image_id}/caption")
async def write_caption(image_id: int, caption: str = "", tags: str = ""):
    parsed = [t.strip() for t in tags.split(",") if t.strip()]
    if len(parsed) > MAX_TAGS or len(caption) > MAX_CAPTION:
        return {"ok": False, "reason": f"at most {MAX_TAGS} tags and {MAX_CAPTION} characters"}
    return _decide(image_id, CAPTION, {"caption": caption, "tags": parsed})


@router.get("/api/tags")
async def tags(limit: int = 100, q: str = ""):
    """Every tag the owner has written, from the log. No tag table."""

    counts: dict[str, int] = {}
    for value in decisions.current(db(), CAPTION).values():
        for tag in (value or {}).get("tags", []) if isinstance(value, dict) else []:
            if not q or q.lower() in str(tag).lower():
                counts[tag] = counts.get(tag, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[: int(limit)]
    return {"tags": [{"tag": name, "count": n} for name, n in ranked]}


@router.get("/api/cache/status")
async def cache_status():
    """What is made, what is owed, and whether chores are running.

    Three facts from two queries. The panel this feeds used to read a 394-line
    status builder that reconciled a tier-allocation profile, four per-size
    budgets, a presence table, an in-flight set and a generation counter --
    all of which described the machinery rather than the answer.
    """

    conn = db()
    return {
        **tiles.status(conn),
        "owed": work.debt(conn),
        "paused": work.paused(conn),
    }


@router.post("/api/cache/pregen/start")
async def resume_chores():
    return _chores(False)


@router.post("/api/cache/pregen/stop")
async def pause_chores():
    return _chores(True)


def _chores(stop: bool) -> dict:
    """Start and stop mean something narrower here, and it is worth being exact.

    Chores already yield while you are using the app, so these do not control
    *when* work happens -- they say whether it should happen at all. That is a
    preference, so it is a decision in the log rather than a flag in a module,
    and it therefore survives a restart. Someone who paused chores to save
    battery would not thank us for resuming them on the next launch.
    """

    conn = _writer()
    try:
        work.set_paused(conn, stop)
        return {"paused": stop, "state": "paused" if stop else "running"}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.post("/api/cache/clear")
async def clear_cache():
    conn = _writer()
    try:
        return await asyncio.to_thread(tiles.clear, conn)
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.get("/api/image/{image_id}/state")
async def state(image_id: int):
    """One of the five words, computed now and never stored."""

    return {"state": photos.state(db(), image_id)}


@router.get("/api/work/owed")
async def owed():
    """What the machine still owes you. A status line, nothing depends on it."""

    return work.debt(db())
