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
import json

from fastapi import APIRouter, Request, Response

import library
import rank
import render
import synchronize
import tiles
import work
import xmp
from core.catalog_path import catalog_path
from data import connection
from model import cache, decisions, photos

router = APIRouter()


def db():
    """A read connection on the loop. Primary-key-shaped reads only."""

    return connection.inline_reader(catalog_path())


def _writer():
    return connection.open_sync(catalog_path())


async def _writing(job, *args, **kwargs):
    """Run a writer off the loop, on a connection opened where it is used.

    A sqlite3 connection belongs to the thread that opened it, so making one on
    the loop and handing it to `to_thread` is a 500 that waits for the route to
    be exercised. That is exactly how it survived: `POST /api/folder/synchronize`
    had the defect from the day it was written and nobody had pressed the button
    until a Lightroom metadata save gave it 149 files to refresh.

    Reads do not need this — `inline_reader` is opened `check_same_thread=False`
    on purpose, because a read-only WAL connection can never hold a write lock.
    """

    def run():
        conn = _writer()
        try:
            return job(conn, *args, **kwargs)
        finally:
            connection.close_sync(conn, db_path=catalog_path())

    return await asyncio.to_thread(run)


def _tile_headers(tag: str, versioned: bool) -> dict:
    """How long a tile may be kept, which depends on what its address names.

    A URL carrying `v` names the bytes it was made from, so it can never go
    stale and may be kept forever without asking. A bare URL points at
    "whatever this photograph looks like now", which changes when an
    application rewrites the file — so it must be revalidated every time.

    This is the rule the old `max-age=31536000` broke by applying the first
    answer to the second kind of address, and those cached copies are why a
    correct server still showed sideways photographs.
    """

    return {"ETag": tag, "Cache-Control": (
        "private, max-age=31536000, immutable" if versioned else "private, no-cache")}


@router.get("/api/thumb/{size}/{image_id}")
async def thumb(size: str, image_id: int, r: int = 0, v: str = "", request: Request = None):
    """One tile. Cache, or make it, or say which of the five words applies.

    `v` is the photograph's identity, read by nothing here — it exists so the
    address changes when the picture does, which is the only thing that can
    reach a cache the browser has already decided is fresh.
    """

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

    turn = int(decisions.latest(conn, row["hash"], decisions.ROTATE) or 0) if row["hash"] else 0
    recipe = {"size": longest, "edits": None, "rotate": turn}
    if row["hash"]:
        entry = cache.get(conn, row["hash"], "tile", recipe)
        body = tiles.read(entry) if entry and entry["state"] == cache.READY else None
        if body:
            tag = f'"{row["hash"]}-{longest}-{turn}"'
            if request is not None and request.headers.get("if-none-match") == tag:
                # Unchanged, so send nothing: the browser asked, and the answer
                # is 304 rather than 300 KB.
                return Response(status_code=304, headers=_tile_headers(tag, bool(v)))
            return Response(content=body, media_type="image/jpeg",
                            headers=_tile_headers(tag, bool(v)))
        if entry and entry["state"] == cache.FAILED:
            return Response(status_code=410)  # unreadable, and we know why

    source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
    if source is None:
        # away or lost, and the difference is whether every drive is here.
        return Response(status_code=409 if photos.state(conn, image_id) == "away" else 404)
    if not row["hash"]:
        return Response(status_code=204)  # preparing: identity is owed first

    made = await asyncio.to_thread(_make_tile, row["hash"], longest, source, turn)
    if made is None:
        return Response(status_code=410)
    return Response(content=made, media_type="image/jpeg",
                    headers=_tile_headers(f'"{row["hash"]}-{longest}-{turn}"', bool(v)))


def _make_tile(hash: str, longest: int, source: str, turn: int = 0) -> bytes | None:
    conn = _writer()
    try:
        entry = cache.make(conn, hash, "tile", source,
                           {"size": longest, "edits": None, "rotate": turn})
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
    # Each root *is* a source. Wrapping them in a synthetic "Library" node put
    # a row reading "Library 0" above everything, which had to be expanded
    # before any folder was visible and whose count could only ever be a lie --
    # a shim standing in for a source concept that no longer exists.
    return {
        "sources": [
            {
                "id": index + 1,
                "path": node["path"],
                "display_name": node["name"],
                "online": True,
                "total_count": node["total_count"],
                "safety": node["safety"],
                "folders": node["children"],
            }
            for index, node in enumerate(tree)
        ],
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


def _apply_rotation(conn, rows: list[dict]) -> None:
    """Report the dimensions the photograph will actually be shown at.

    A quarter turn swaps width and height, and the grid sizes each cell from
    these numbers. Leaving them unrotated is why a turned film scan sat
    sideways inside a landscape cell: the tile was right and the hole it went
    into was wrong.

    One query for the page rather than one per photograph -- the rotations are
    decisions, so they come back in a single `IN`.
    """

    hashes = [row["hash"] for row in rows if row.get("hash")]
    if not hashes:
        return
    holes = ",".join("?" for _ in hashes)
    turned = {
        row["subject"]: int(json.loads(row["value"]) or 0)
        for row in conn.execute(
            f"""
            SELECT subject, value FROM (
                SELECT subject, value,
                       ROW_NUMBER() OVER (PARTITION BY subject ORDER BY at DESC, id DESC) AS rank
                FROM decisions WHERE family = 'rotate' AND subject IN ({holes})
            ) WHERE rank = 1
            """,
            hashes,
        )
    }
    for row in rows:
        turn = turned.get(row.get("hash"), 0) % 360
        if not turn:
            continue
        # The turn always travels, because the tile URL carries it and a tile
        # rendered at 180 is a different picture from one rendered at 0. Only a
        # quarter turn swaps the shape of the cell it goes in.
        row["rotate"] = turn
        if turn % 180 == 90:
            row["width"], row["height"] = row["height"], row["width"]


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
    _apply_rotation(conn, rows)
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
    return await _writing(tiles.clear)


@router.post("/api/image/{image_id}/rotate")
async def rotate(image_id: int, degrees: int = 90, absolute: bool = False):
    """Turn a photograph the file itself gets wrong.

    A correction *on top of* the file, not a replacement for it. `decode` has
    already applied whatever the photograph says about itself, so `0` here is
    not a claim that the frame is upright — it is the absence of a correction,
    and the file has the last word. That is what makes this safe to apply to
    something Lightroom has already turned.

    Being a decision it goes in the log, survives the row being rebuilt, and
    never touches the original file.
    """

    conn = _writer()
    try:
        row = conn.execute(
            "SELECT content_hash AS hash FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        if row is None or not row["hash"]:
            return {"ok": False, "reason": "this photo has no identity yet"}
        was = int(decisions.latest(conn, row["hash"], decisions.ROTATE) or 0)
        now = int(degrees) % 360 if absolute else (was + int(degrees)) % 360
        decisions.decide(conn, row["hash"], decisions.ROTATE, now)
        conn.commit()
        return {"ok": True, "rotate": now, "was": was}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


# There is deliberately no folder-wide rotate. It read as a labour saver --
# "a lab scans a roll the same way round" -- and it is true right up to the
# moment the photographer turns individual frames, which is the only reason
# the roll needed attention at all. Pointed at one 40-frame roll it wrote 40
# corrections over Lightroom's 18, and the ~21 upright frames came back
# sideways. A verb that is wrong exactly when it is used is not a shortcut.


@router.post("/api/folder/read-sidecars")
async def read_sidecars(folder: str = "", limit: int = 5000):
    """Take in what another application wrote beside these photographs.

    Lightroom calls this Read Metadata from Files, and it means the same thing
    here: stars, colour labels and orientation written by Lightroom, darktable
    or Bridge are decisions, so they join the log with the sidecar's own
    timestamp.

    That timestamp is the whole precedence rule. A star you set here yesterday
    beats a sidecar written last week; a sidecar written this morning beats a
    star you set last year. Nothing needs a merge strategy because the log is
    already ordered, and `latest()` was always going to answer this correctly.
    """

    conn = _writer()
    try:
        args: list = []
        where = "i.content_hash IS NOT NULL AND i.tail IS NOT NULL"
        if folder:
            prefix = str(folder).replace("\\", "/").strip("/") + "/"
            where += " AND substr(i.tail, 1, ?) = ?"
            args += [len(prefix), prefix]
        rows = conn.execute(
            f"SELECT i.id, i.tail, i.file_size FROM images i WHERE {where} LIMIT ?",
            (*args, int(limit)),
        ).fetchall()

        read = found = 0
        for row in rows:
            source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
            if source is None:
                continue
            read += 1
            if xmp.adopt(conn, row["id"], source):
                found += 1
        conn.commit()
        return {"ok": True, "folder": folder or "everything",
                "photographs": len(rows), "readable": read, "with sidecars": found}
    finally:
        connection.close_sync(conn, db_path=catalog_path())


@router.get("/api/folder/synchronize")
async def synchronize_plan(folder: str = ""):
    """What synchronizing this folder would change. Writes nothing.

    The GET is the dialog and the POST is the button, which is the whole point:
    a scan that acts on its own is one you cannot trust with a drive that was
    briefly unplugged.
    """

    return await asyncio.to_thread(synchronize.plan, db(), folder)


@router.post("/api/folder/synchronize")
async def synchronize_apply(folder: str = "", adopt: bool = True,
                            refresh: bool = True, forget: bool = False):
    """Act on the plan. Adopting and refreshing are on by default; forgetting is not.

    They are not symmetrical and should not be. Adopting adds rows for files
    that exist and refreshing re-reads facts about files that changed — both
    only ever tell the catalog something the disk already knows. Forgetting
    takes a photograph out of the library, so it is opt-in and can only ever
    touch the `missing` list — which is empty whenever any drive is away.
    """

    return await _writing(synchronize.apply, folder,
                          adopt=adopt, refresh=refresh, forget=forget)


@router.get("/api/image/{image_id}/state")
async def state(image_id: int):
    """One of the five words, computed now and never stored."""

    return {"state": photos.state(db(), image_id)}


@router.get("/api/work/owed")
async def owed():
    """What the machine still owes you. A status line, nothing depends on it."""

    return work.debt(db())
