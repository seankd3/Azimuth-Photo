"""The V2 product boundary: open the library, answer it, close it.

There is no environment bootstrap, router graph, singleton connection, or V1
schema prelude here. The desktop owns one ``Library`` instance and calls these
operations from whichever native surface replaces the current shell.
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time
from typing import Callable, TypeVar

import develop as developing
import embed
import faces
import library as queries
import metadata as embedded_metadata
from photo import tags
import model
import photostats
import places
import rank
import render
import search as finding
import sharpness
import stacks
import tiles
import work
from model import cache, copies, criteria, cull, decisions, drives, intake, photos, sets, trash
from model.scope import EVERYTHING, Scope, all_of, any_of, covers_only, folded, folder as in_folder, ids as these, outside, where as scope_where

log = logging.getLogger(__name__)

Result = TypeVar("Result")
# How many photographs a window may say it is looking at. A viewport holds a
# few dozen; the bound keeps the worker's ORDER BY small.
LOOKING_AT_MOST = 400


def attached_now(conn) -> dict[int, str]:
    """The drives attached right now and where each one is, answered by
    looking at each marker once. Every read that follows -- a page, the
    worker's next step, locating a photograph -- answers from this instead
    of touching a disk."""

    here = {}
    for row in conn.execute("SELECT id, uuid FROM drives"):
        root = drives.root_of(conn, row["uuid"])
        if root is not None:
            here[int(row["id"])] = root
    return here


def repair(conn) -> None:
    """Bring the derived columns back to what the log and the cache say.

    The sharpness rows of a measure no longer asked for go here too: never
    evicted, never read, they would sit in the catalog for good.

    Every column that is an index over something else -- the metadata
    columns over the cache, `develop` over the log, `stack_of` over the
    dates -- is rebuilt here, writing only the rows that differ, so a start
    where nothing drifted writes nothing. Runs on the sweep lane behind the
    first paint: at 150,000 rows the reads alone are seconds, and the
    window used to wait for them before it opened.
    """

    # The residue rules below are one-shot repairs of retired rules; once a
    # catalog has been through them they cost a scan for nothing, so the
    # log remembers which version of them ran.
    RESIDUE = "residue-2026-09-10"
    residue_done = decisions.latest(conn, work.MACHINE, "repaired") == RESIDUE
    sharpness.tidy(conn)
    # Residue of the retired rule that stored a projection failure as the
    # photograph's answer: "failed" rows with no value, poisoned by a
    # moment's lock contention, which stopped 27 real photos from ever
    # learning their shape. Dropping them re-owes the work.
    if not residue_done:
        conn.execute(
            "DELETE FROM cache WHERE state = 'failed' AND value IS NULL"
            " AND note LIKE 'ProjectionError:%'")
    # A camera raw always carries a date, so a dateless metadata answer for
    # one is an old reader's -- CR3s read with IFD0's map before the CMT2
    # fix (657 rows), DNGs whose Exif IFD sat past the old bounded read
    # (2,086 rows: Lightroom's converter writes it after the image data).
    # Dropping the answer re-owes the read to the fixed reader.
    if not residue_done:
        conn.execute(
            "DELETE FROM cache WHERE kind = 'metadata'"
            " AND value NOT LIKE '%date_taken%'"
            " AND hash IN (SELECT content_hash FROM images"
            "              WHERE file_ext IN ('.cr3', '.dng')"
            "                AND content_hash IS NOT NULL)")
        # Answers from a reader older than this one (no exposure facts) are
        # re-owed to the current reader. No-op once every row carries its version.
        conn.execute(
            "DELETE FROM cache WHERE kind = 'metadata' AND recipe = '{}'"
            " AND value NOT LIKE '%\"v\":2%'")
        decisions.decide(conn, work.MACHINE, "repaired", RESIDUE)
    conn.commit()
    # The metadata columns are rebuilt only when a metadata answer has
    # arrived since the last rebuild: the log keeps the newest answer's
    # time, and a start where nothing changed skips 150,000 decodes.
    newest = conn.execute(
        "SELECT MAX(at) FROM cache WHERE kind = ?", (embedded_metadata.KIND.name,)).fetchone()[0]
    if newest is None or decisions.latest(conn, work.MACHINE, "metadata-projected") != newest:
        embedded_metadata.reindex(conn)
        if newest is not None:
            decisions.decide(conn, work.MACHINE, "metadata-projected", newest)
            conn.commit()
    developing.reindex(conn)
    stacks.project(conn)


def cameras_of(conn) -> list[dict]:
    """What the camera chip offers: every model in the library, counted."""

    return [dict(row) for row in conn.execute(
        "SELECT camera_model AS model, COUNT(*) AS photos FROM images"
        " WHERE camera_model IS NOT NULL AND status != 'trashed'"
        " GROUP BY camera_model ORDER BY photos DESC")]


def shape_stamp(conn) -> tuple:
    """What the facets depend on, cheaply: the live and trashed counts. A
    cull, a forget or a sweep moves one of them."""

    return (int(conn.execute(f"SELECT COUNT(*) FROM images i WHERE {queries.IN_LIBRARY}").fetchone()[0]),
            trash.count(conn))


def facets_of(conn) -> dict:
    """The library's shape, for the search drop: years, cameras, the top
    roots, and orientations, each counted in identities. Every entry is a
    fact the chip language can say back, so a card is a chip. Five passes
    over the library (530 ms at 150k rows), which is why the sweep lane
    makes it and the window reads the made answer."""

    ask = conn.execute
    live = queries.IN_LIBRARY
    years = [
        {"year": row[0], "photos": row[1]}
        for row in ask(
            "SELECT substr(i.date_taken, 1, 4) AS y, COUNT(*)"
            f" FROM images i WHERE {live} AND i.date_taken IS NOT NULL"
            " GROUP BY y ORDER BY y DESC")
        if row[0] and len(row[0]) == 4
    ]
    shown_w = "CASE WHEN i.rotate IN (90, 270) THEN i.height ELSE i.width END"
    shown_h = "CASE WHEN i.rotate IN (90, 270) THEN i.width ELSE i.height END"
    orientations = [
        {"orientation": row[0], "photos": row[1]}
        for row in ask(
            f"SELECT CASE WHEN {shown_w} > {shown_h} THEN 'landscape'"
            f" WHEN {shown_w} < {shown_h} THEN 'portrait' ELSE 'square' END AS o,"
            " COUNT(*)"
            f" FROM images i WHERE {live} AND i.width > 0 AND i.height > 0"
            " GROUP BY o ORDER BY 2 DESC")
    ]
    roots = [
        {"folder": row[0], "photos": row[1]}
        for row in ask(
            "SELECT substr(i.tail, 1, instr(i.tail, '/') - 1) AS root,"
            " COUNT(*)"
            f" FROM images i WHERE {live} AND instr(i.tail, '/') > 0"
            " GROUP BY root ORDER BY 2 DESC")
    ]
    return {"years": years, "cameras": cameras_of(conn),
            "orientations": orientations, "roots": roots}


class Library:
    """One open catalog, its tile store, and its one background worker."""

    def __init__(self, catalog_path: str, tile_root: str):
        self.catalog_path = os.path.abspath(os.fspath(catalog_path))
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        self.tiles = tiles.Store(tile_root)
        # The vector kind: computed from the grid tile, made only where the
        # model runs, never evicted. On the worker like any other kind, so a
        # fresh library grows its own space photo by photo.
        self.space = embed.kind(self.tiles)
        # Faces ride the same worker: found on the CPU, kept forever, and
        # clustered into people on the rank lane's rhythm.
        self.looking_at_people = faces.kind(self.tiles)
        # And the photographic facts the embedding throws away — palette,
        # tone, sharpness, and the color/bw/sepia word the Look chip reads.
        self.knowing_looks = photostats.kind(self.tiles)
        # Where the sharpness sits, per photograph and per face: computed on
        # the CPU behind the tiles and the faces, read by the inspector.
        self.knowing_sharpness = sharpness.kind(self.tiles, self.looking_at_people)
        self.conn = model.connect(self.catalog_path)
        # Which drives are here, by their markers. Looked at once now and by
        # the follower every few seconds, so a page read never probes a disk
        # -- an absent share made every page wait on its timeout.
        self.here: dict[int, str] = attached_now(self.conn)
        # The search drop's shape with the stamp it was made at, made on the
        # sweep lane after a sweep that changed something or on first ask;
        # a stamp that moved (a cull, a forget) remakes it.
        self._facets: tuple | None = None
        # The folder tree, the same way: a second at 150k tails, made on the
        # sweep lane after a sweep that changed something, keyed by the
        # stamp and the sweep count since only a sweep or a cull moves it.
        self._tree: tuple | None = None
        # The last search's ranked ids with what they were asked of: a page
        # and the two-second re-read are slices of it, never the fusion
        # again. One entry, because paging is always the last search.
        self._found: tuple | None = None
        self._refacet_pending = False
        self._owner = None   # the OwnedLibrary, once one holds this
        # What the window says it is looking at, most recent statement wins.
        # Read by the worker on every step, so the first tiles made are the
        # ones on screen; nothing else about the worker's order changes.
        self._looking: tuple[int, ...] = ()
        # Sweeps of the folders completed since start, for the window's pulse.
        self.swept = 0
        self.chores = work.Chores(
            lambda: model.connect(self.catalog_path),
            (embedded_metadata.KIND, *self.tiles.kinds, self.space,
             self.looking_at_people, self.knowing_looks, self.knowing_sharpness),
            on_screen=lambda: self._looking,
            attached=lambda: self.here,
            ceiling_bytes=self.tiles.ceiling_bytes,
            # A decode is one core for a third of a second; a quarter of the
            # machine's threads keeps the interactive lane and the disk free.
            lanes=max(1, (os.cpu_count() or 4) // 4),
        )
        self._closed = False

    def __enter__(self) -> "Library":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        """Release the worker before its catalog, once."""

        if self._closed:
            return
        if not self.chores.stop():
            raise TimeoutError("background work did not release the catalog")
        self.conn.close()
        self._closed = True

    def start(self) -> bool:
        self._open()
        return self.chores.start()

    def attach(self, root: str, *, label: str = "", is_record: bool = False) -> dict:
        """Remember one chosen folder immediately; scanning is a separate verb."""

        self._open()
        said = drives.attach(self.conn, root, label=label, is_record=is_record)
        self.here = attached_now(self.conn)
        return said

    def refresh(self, drive_uuid: str) -> dict:
        self._open()
        try:
            said = copies.sweep(self.conn, drive_uuid)
            root = drives.root_of(self.conn, drive_uuid)
            if said.get("applied") and root:
                said["edits_adopted"] = developing.adopt(
                    self.conn, root, said["sidecars"], said.get("changed") or ())
                stacks.project(self.conn)
            return said
        finally:
            self.chores.nudge()

    def attached(self) -> list[dict]:
        self._open()
        return [
            {**dict(row), "attached": int(row["id"]) in self.here}
            for row in self.conn.execute("SELECT * FROM drives ORDER BY id")
        ]

    def viewing(self, view: dict | None) -> Scope:
        """One scope from what the window says it is looking at: a folder, a
        album (with everything shelved under it), and the filter chips.
        Every surface -- grid, count, Rank, search -- narrows by this one
        answer, which is what keeps them the same view."""

        self._open()
        view = view or {}
        parts = []
        held = [str(f) for f in (view.get("folders") or ()) if str(f)]
        if held:
            # Several folders are one view — a shoot that spanned two days
            # is browsed as their union, same as a folder chip with two
            # values.
            parts.append(any_of(*(in_folder(f) for f in held)))
        if view.get("album"):
            parts.append(self._shelf(str(view["album"])))
        if view.get("chips"):
            parts.append(criteria.compile(self.conn, view["chips"]))
        if view.get("ids"):
            # A survey: the marked photographs and nothing else, so a burst
            # can be judged in a round of its own.
            parts.append(these([int(i) for i in view["ids"]]))
        # Stacks are open unless the person collapsed them: collapsed, the
        # members wait behind their cover except the covers opened; open,
        # they sit in place except the covers folded. A stack chip is the
        # step inside one, so it lifts either.
        if not any(c.get("is") == "stack" for c in (view.get("chips") or ())):
            parts.append(covers_only(view.get("expanded") or ()) if view.get("collapsed")
                         else folded(view.get("folded") or ()))
        return all_of(*parts)

    def _shelf(self, set_id: str) -> Scope:
        """An album and everything shelved under it. The shelf is the
        name: `America/Utah` sits under `America`, so browsing a parent is
        the union of its own answer and its descendants' -- the 07-09 ruling,
        derived from names the way the folder tree derives from tails."""

        said = sets.describe(self.conn, set_id)
        if said is None:
            return criteria.resolve(self.conn, set_id)
        prefix = str(said.get("name", "")) + "/"
        under = [entry["id"] for entry in sets.all(self.conn, kind=sets.ALBUM)
                 if str(entry.get("name", "")).startswith(prefix)]
        return any_of(*(criteria.resolve(self.conn, sid) for sid in (set_id, *under)))

    def browse(
        self,
        *,
        scope: Scope = EVERYTHING,
        sort: str = "newest",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        self._open()
        return self._with_urls(queries.photos(
            self.conn, scope=scope, sort=sort, limit=limit, offset=offset,
            renditions=self.tiles.renditions, reachable_on=self._here(),
        ))

    def _here(self) -> list[int]:
        return list(self.here)

    def size(self, scope: Scope = EVERYTHING) -> int:
        self._open()
        return queries.size(self.conn, scope)

    def position(self, photo_id: int, sort: str, view: dict | None = None) -> int | None:
        self._open()
        return queries.position(self.conn, int(photo_id), sort, self.viewing(view))

    def identifiers(self, scope: Scope = EVERYTHING, *, trashed: bool = False) -> list[int]:
        """Every photo id the scope holds — what Select All means, which is
        the view's whole answer and never just the pages a scroller loaded."""

        self._open()
        clause, args = scope_where(scope)
        living = "i.status = 'trashed'" if trashed else queries.IN_LIBRARY
        return [row["id"] for row in self.conn.execute(
            f"SELECT i.id FROM images i WHERE {living} AND ({clause})", args)]

    def folders(self) -> list[dict]:
        """One tree over every drive, each node with its count and whether
        everything under it has a copy on a record drive. The made answer,
        as `facets` is: made here only the first time; a key that moved asks
        the sweep lane for a fresh one and answers with the last meanwhile."""

        self._open()
        key = (shape_stamp(self.conn), self.swept)
        if self._tree is None:
            self._tree = (key, queries.folder_tree(self.conn, attached=self.here))
        elif self._tree[0] != key:
            self._refacet()
        return self._tree[1]

    def look(self, photo_ids) -> int:
        """The window says which photographs it is showing; the worker makes
        their tiles first. Returns how many it will remember."""

        wanted = tuple(int(i) for i in photo_ids if int(i) > 0)[:LOOKING_AT_MOST]
        self._looking = wanted
        self.chores.nudge()
        return len(wanted)

    def _with_urls(self, rows: list[dict]) -> list[dict]:
        # A tile is a file the window reads straight from disk, so a rendition
        # crosses the boundary as its URL and nothing is decoded on this lane.
        for row in rows:
            for name in self.tiles.renditions:
                row[name] = Path(row[name]).as_uri() if row.get(name) else None
        return rows

    def counts(self) -> dict[str, int]:
        self._open()
        return queries.counts(self.conn)

    def changed(self, said):
        """The library's answer moved under the window -- rows left or came
        back, counts and chapters with them. `swept` is the window's cue to
        re-read what it holds, whoever moved it: a sweep, a cull, a forget.
        The facets are remade off this lane: the window keeps reading the
        last answer while the sweep lane makes the next."""

        self.swept += 1
        if self._owner is None:
            self._facets = self._tree = None   # made on the next ask
        else:
            self._refacet()
        return said

    def _reshape(self, conn, swept: int | None = None) -> None:
        """The library's shape, remade: the facets and the folder tree, on
        whichever lane called. `swept` is the count the tree is for, when the
        caller is about to move it."""

        self._facets = (shape_stamp(conn), facets_of(conn))
        self._tree = ((self._facets[0], self.swept if swept is None else swept),
                      queries.folder_tree(conn, attached=self.here))

    def _refacet(self) -> None:
        """Remake the shape on the sweep lane, at most one in flight."""

        if self._owner is None:
            self._reshape(self.conn)   # no lane to make it on: made here, now
            return
        if self._refacet_pending:
            return
        self._refacet_pending = True

        def make():  # noqa: D401
            try:
                conn = model.connect(self.catalog_path)
                try:
                    self._reshape(conn)
                finally:
                    conn.close()
            except Exception:
                log.exception("the shape could not be remade")
            finally:
                self._refacet_pending = False

        try:
            self._owner.submit_sweep(make)
        except Exception:  # noqa: BLE001 -- the lane is closing; the next ask remakes
            self._refacet_pending = False

    def pick(self, photo_ids) -> dict:
        self._open()
        return cull.pick(self.conn, photo_ids)

    def clear_pick(self, photo_ids) -> dict:
        self._open()
        return cull.clear(self.conn, photo_ids)

    def reject(self, photo_ids) -> dict:
        self._open()
        return self.changed(cull.reject(self.conn, photo_ids))

    def restore(self, photo_ids) -> dict:
        self._open()
        return self.changed(cull.restore(self.conn, photo_ids))

    def undo_cull(self, changes) -> dict:
        self._open()
        return self.changed(cull.undo(self.conn, changes))

    def turn(self, photo_ids, by: int = 90) -> dict:
        self._open()
        return cull.turn(self.conn, photo_ids, by=int(by))

    def stack(self, photo_ids) -> dict:
        """These photographs become one stack; one photograph means the
        cadence run around it."""

        self._open()
        return self.changed(stacks.stack(self.conn, photo_ids, tiles=self.tiles))

    def unstack(self, photo_ids) -> dict:
        self._open()
        return self.changed(stacks.unstack(self.conn, photo_ids))

    def _photo_row(self, photo_id: int):
        row = self.conn.execute(
            "SELECT content_hash AS hash, tail, file_size, develop FROM images WHERE id = ?",
            (int(photo_id),)).fetchone()
        if row is None or not row["hash"]:
            raise ValueError("that photograph has no identity yet")
        return row

    def develop(self, photo_id: int, patch: dict) -> dict:
        """The owner changes one photograph's edit: Lightroom's own keys,
        None removing one.

        The decision lands first; then the edited loupe and grid publish
        immediately when the plain loupe file is already here, so the edit
        is on screen before the worker has turned around. The worker owns
        whatever could not be made now.
        """

        self._open()
        row = self._photo_row(photo_id)
        held = {}
        for key, value in dict(patch or {}).items():
            key = str(key)
            if key not in developing.RENDERED:
                raise ValueError(f"not a setting this build renders: {key}")
            held[key] = value
        crop = [held.get(key) for key in developing.CROP_KEYS]
        if any(v is not None for v in crop) and not all(v is None for v in crop):
            left, top, right, bottom = ((float(v) if v is not None else None) for v in crop)
            if None not in (left, top, right, bottom) and not (
                    0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
                raise ValueError("a crop keeps some of the photograph")
        developing.edit(self.conn, row["hash"], held)
        current = self.conn.execute(
            "SELECT develop FROM images WHERE id = ?", (int(photo_id),)).fetchone()["develop"]
        self._prune_renditions(row["hash"], keep=current)
        made = False
        if current is not None:
            # The grid answer publishes now — the pipeline at grid size is
            # a beat, not a wait — and the worker owns the loupe behind it:
            # at loupe size the same render is tens of seconds, and this
            # verb holds the library's one lane while it runs.
            edit = developing.parts(current)
            source = photos.locate(self.conn, row["tail"], expected_size=row["file_size"])
            if source or os.path.isfile(self.tiles.path(row["hash"], render.GRID))                     or os.path.isfile(self.tiles.path(row["hash"], render.LOUPE)):
                cache.make(self.conn, row["hash"], self.tiles.grid, source or "", {"edit": edit})
                made = True
        self.chores.nudge()
        return {"develop": current, "made": made}

    def develop_preview(self, photo_id: int, patch: dict, size: int = 1280) -> str:
        """One look at an uncommitted edit: the current settings plus this
        patch, rendered over a held base, published as a file the window
        loads like any tile. No decision is written — this is the slider
        moving under the thumb.

        The loupe file decodes once per photograph and the fitted base is
        held; each look pays only the pipeline (the decode was ~3 s of a
        3.5 s look). The answer is a file, never inline bytes: a data URI
        crossing the bridge went silent past a size the bridge never
        names, and a look that sometimes arrives is worse than none. Two
        names alternate so the window never reads a half-written frame.
        """

        self._open()
        row = self._photo_row(photo_id)
        size = max(256, min(2048, int(size)))
        key = (row["hash"], size)
        if getattr(self, "_preview_key", None) != key:
            # The scrub is a proxy render, as Lightroom's own is: the grid
            # tile decodes in tens of milliseconds where the loupe costs
            # seconds, and a first look that outlives the bridge's patience
            # arrives as no look at all.
            plain = self.tiles.path(row["hash"], render.GRID)
            if size > render.GRID or not os.path.isfile(plain):
                plain = self.tiles.path(row["hash"], render.LOUPE)
            if not os.path.isfile(plain):
                raise ValueError("the full picture is not here yet")
            with render.Image.open(plain) as source:
                self._preview_base = render.fit(source.convert("RGB"), size)
            self._preview_key = key
        held = dict(developing.settings(self.conn, row["hash"]))
        for key_name, value in dict(patch or {}).items():
            if value is None:
                held.pop(str(key_name), None)
            else:
                held[str(key_name)] = value
        edit = developing.parts(developing.fragment(held))
        image = render.developed(self._preview_base, edit) if edit else self._preview_base
        looks = getattr(self, "_preview_looks", 0) + 1
        self._preview_looks = looks
        target = os.path.join(self.tiles.root, f".look-{looks % 2}.jpg")
        with open(target, "wb") as handle:
            handle.write(render.encode(image, quality=82))
        return Path(target).as_uri() + f"?look={looks}"

    def export_settings(self, photo_ids=None, folder: str = "") -> dict:
        """The photograph's whole story — settings, stars, pick, words —
        written to sidecars beside the files; everything else a sidecar
        holds is kept. Given a folder instead of ids, the whole tree
        changes hands in one click."""

        self._open()
        if photo_ids is None:
            clause, args = scope_where(in_folder(folder) if folder else EVERYTHING)
            photo_ids = [row["id"] for row in self.conn.execute(
                f"SELECT i.id FROM images i WHERE {queries.IN_LIBRARY} AND ({clause})", args)]
        written = unchanged = missing = 0
        for photo_id in photo_ids:
            row = self.conn.execute(
                "SELECT content_hash AS hash, tail, file_size FROM images WHERE id = ?",
                (int(photo_id),)).fetchone()
            if row is None or not row["hash"]:
                missing += 1
                continue
            source = photos.locate(self.conn, row["tail"], expected_size=row["file_size"])
            if source is None:
                missing += 1
                continue
            said = developing.write_sidecar(self.conn, row["hash"], source)
            if said["status"] == "written":
                written += 1
            elif said["status"] == "unchanged":
                unchanged += 1
        return {"written": written, "unchanged": unchanged, "missing": missing}

    def develop_state(self, photo_id: int) -> dict:
        """What the editing surfaces need: the full-frame loupe, the crop
        worn, and the current settings in Lightroom's spelling."""

        self._open()
        row = self._photo_row(photo_id)
        plain = self.tiles.path(row["hash"], render.LOUPE)
        box = developing.crop_of(row["develop"])
        return {
            "plain": Path(plain).as_uri() if os.path.isfile(plain) else None,
            "box": list(box) if box else None,
            "settings": developing.parts(developing.fragment(
                developing.settings(self.conn, row["hash"]))),
        }

    def _prune_renditions(self, digest: str, keep: str | None) -> None:
        """Drop rendition rows for edits this photograph no longer wears.

        The grid tile is never evicted, so a superseded edit's rows would
        otherwise sit forever beside the current answer. A worker mid-compute
        on the old recipe can land one zombie row after this runs; it is
        invisible — no join ever builds that recipe again — and the next
        edit of the same photograph prunes it, so it is left alone rather
        than guarded against."""

        kinds = {kind.name: kind for kind in self.tiles.kinds}
        marks = ",".join("?" for _ in kinds)
        rows = self.conn.execute(
            f"SELECT kind, recipe, path FROM cache WHERE hash = ? AND kind IN ({marks})"
            f" AND recipe != '{{}}'",
            (str(digest), *kinds),
        ).fetchall()
        wanted = f'{{"edit":{keep}}}' if keep else None
        for row in rows:
            if row["recipe"] == wanted:
                continue
            if row["path"]:
                kinds[row["kind"]].remove(row["path"])
            self.conn.execute(
                "DELETE FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
                (str(digest), row["kind"], row["recipe"]))
        self.conn.commit()

    def search(self, query: str, *, limit: int = 200, offset: int = 0,
               space=None, query_vector=None, view: dict | None = None,
               like=()) -> dict:
        """Find photographs: the fused ranking, one page of it as rows.

        The whole answer is capped at 500 -- a search whose five hundredth
        result matters is a browse, and the folder tree is better at it.

        `like` asks with photographs instead of words: the query becomes the
        selection's centre in the space -- no model involved, their vectors
        are already in the matrix -- and the seeds themselves stay out of
        the answer.
        """

        self._open()
        omit: set = set()
        if like:
            held = self._identities(like)
            query_vector = self._centre(held, space)
            omit = set(held)
        # A taught word is smarter wherever it is used: searching it never
        # answers with what the owner said it is not.
        import labels as taught

        omit |= set(taught.denied(self.conn, query))
        # What the answer depends on: the words, the seeds, the view, the
        # space (count-keyed, as its memo is), whether the words had a
        # vector, the library's shape, and what is denied.
        key = (query, tuple(sorted(like)), json.dumps(view, sort_keys=True, default=str),
               len(space[0]) if space else None, query_vector is not None,
               shape_stamp(self.conn), self.swept, frozenset(omit),
               self.conn.execute("SELECT MAX(id) FROM decisions").fetchone()[0])
        if self._found is None or self._found[0] != key:
            self._found = (key, finding.search(
                self.conn, query, space=space, query_vector=query_vector,
                scope=self.viewing(view), omit=frozenset(omit)))
        ranked = self._found[1]
        page = ranked[int(offset):int(offset) + max(1, int(limit))]
        rows = {row["id"]: row for row in queries.photos(
            self.conn, scope=these(page), sort="newest", limit=max(1, len(page)),
            offset=0, renditions=self.tiles.renditions, reachable_on=self._here(),
        )} if page else {}
        return {"total": len(ranked), "photos": [rows[i] for i in page if i in rows]}

    def days(self, view: dict | None = None) -> list[dict]:
        """The grid's chapters: every day in the view with its count, in
        exactly the newest-sort order, so the window can lay headers by a
        running sum."""

        self._open()
        return queries.days(self.conn, self.viewing(view))

    def sessions(self) -> list[dict]:
        """The latest shoots, named — the search drop's session cards."""

        self._open()
        return queries.sessions(self.conn)

    # ---- albums ----

    QUICK = "quick"
    LAST_IMPORT = "last-import"
    PINNED = {QUICK: "Quick album", LAST_IMPORT: "Previous import"}

    def albums(self) -> list[dict]:
        """Every album for the sidebar: id, name, whether it is smart,
        and how many photographs it answers with right now. The two pinned
        sets are always present, so the shelf never looks broken before
        first use."""

        self._open()
        for set_id, name in self.PINNED.items():
            said = sets.describe(self.conn, set_id)
            if said is None:
                sets.create(self.conn, name, set_id=set_id)
            elif said.get("name") != name:
                # The pinned rows cannot be renamed by hand, so a differing
                # name is only ever an older vocabulary; it follows quietly.
                sets.rename(self.conn, set_id, name)
        self.conn.commit()
        out = []
        for entry in sets.all(self.conn, kind=sets.ALBUM):
            # The number on the row is the number the row opens to: the
            # shelf's union, not the parent's own members alone.
            clause, args = scope_where(all_of(
                Scope(queries.IN_LIBRARY), self._shelf(entry["id"])))
            count = int(self.conn.execute(
                f"SELECT COUNT(*) FROM images i WHERE {clause}", args
            ).fetchone()[0])
            out.append({
                "id": entry["id"], "name": entry["name"],
                "smart": bool(entry.get("criteria")), "criteria": entry.get("criteria"),
                "pinned": entry["id"] in self.PINNED, "count": count,
            })
        return out

    def _taken_name(self, name: str, but: str | None = None) -> None:
        held = str(name).strip().lower()
        for entry in sets.all(self.conn, kind=sets.ALBUM):
            if entry["id"] != but and str(entry.get("name", "")).lower() == held:
                raise ValueError(f"An album called “{name}” is already there.")

    def create_album(self, name: str, chips=None) -> dict:
        self._open()
        self._taken_name(name)
        set_id = sets.create(self.conn, name, criteria=chips)
        self.conn.commit()
        return {"id": set_id, "name": str(name).strip(), "smart": bool(chips)}

    def rename_album(self, set_id: str, name: str) -> dict | None:
        """Rename the album — and the shelf under it. `America/Utah`
        means nothing once `America` is `USA`, so the children's prefixes
        follow in the same commit."""

        self._open()
        self._taken_name(name, but=set_id)
        was = sets.describe(self.conn, set_id)
        said = sets.rename(self.conn, set_id, name)
        followed = 0
        if said is not None and was is not None:
            prefix = str(was.get("name", "")) + "/"
            for entry in sets.all(self.conn, kind=sets.ALBUM):
                held = str(entry.get("name", ""))
                if entry["id"] != set_id and held.startswith(prefix):
                    sets.rename(self.conn, entry["id"], str(name).strip() + "/" + held[len(prefix):])
                    followed += 1
        self.conn.commit()
        return {**said, "followed": followed} if said is not None else None

    def forget_album(self, set_id: str) -> bool:
        self._open()
        if set_id in self.PINNED:
            raise ValueError("the pinned albums stay")
        gone = sets.forget(self.conn, set_id)
        self.conn.commit()
        return gone

    def remember_album(self, set_id: str) -> bool:
        self._open()
        back = sets.remember(self.conn, set_id)
        self.conn.commit()
        return back

    @staticmethod
    def _centre(identities, space):
        """Where a selection sits in the space: the normalized mean of its
        vectors, or None when none of them have one yet."""

        if space is None:
            return None
        subjects, matrix = space
        if matrix is None or not len(subjects):
            return None

        import numpy as np

        placed = {subject: i for i, subject in enumerate(subjects)}
        rows = [placed[held] for held in identities if held in placed]
        if not rows:
            return None
        centre = matrix[rows].mean(axis=0)
        norm = np.linalg.norm(centre)
        return centre / norm if norm else None

    def _identities(self, photo_ids) -> list[str]:
        wanted = sorted({int(i) for i in photo_ids if int(i) > 0})
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        found = [str(row[0]) for row in self.conn.execute(
            f"SELECT DISTINCT content_hash FROM images WHERE id IN ({marks})"
            " AND content_hash IS NOT NULL", wanted)]
        if not found:
            raise ValueError("those photographs have no identity yet")
        return found

    def _album(self, set_id: str) -> dict:
        said = sets.describe(self.conn, set_id)
        if said is None:
            raise ValueError("no such album")
        return said

    def add_to_album(self, set_id: str, photo_ids) -> dict:
        """Into the album — and on a smart one, pinned in past its rules.
        The same membership row either way; resolve() reads the exceptions."""

        self._open()
        self._album(set_id)
        wanted = self._identities(photo_ids)
        # The count reported is what actually joined, not what was handed in.
        newly = len(set(wanted) - set(sets.members(self.conn, set_id)))
        sets.add(self.conn, set_id, wanted)
        self.conn.commit()
        return {"added": newly}

    def remove_from_album(self, set_id: str, photo_ids) -> dict:
        """Out of the album — and on a smart one, excluded past its rules."""

        self._open()
        self._album(set_id)
        removed = sets.remove(self.conn, set_id, self._identities(photo_ids))
        self.conn.commit()
        return {"removed": removed}

    def quick(self, photo_ids) -> dict:
        """Toss the selection into the Quick album -- or, if every one
        of them is already there, take them back out. One key either way."""

        self._open()
        if sets.describe(self.conn, self.QUICK) is None:
            sets.create(self.conn, self.PINNED[self.QUICK], set_id=self.QUICK)
        identities = self._identities(photo_ids)
        held = set(sets.members(self.conn, self.QUICK))
        if set(identities) <= held:
            moved = sets.remove(self.conn, self.QUICK, identities)
            verb = "removed"
        else:
            moved = sets.add(self.conn, self.QUICK, identities)
            verb = "added"
        self.conn.commit()
        return {verb: moved, "count": len(sets.members(self.conn, self.QUICK))}

    def freeze_album(self, set_id: str) -> dict:
        """A smart album becomes plain: its current answer is written as
        membership and the rules leave. From here it is edited by hand --
        the album you are about to share elsewhere."""

        self._open()
        said = sets.describe(self.conn, set_id)
        if said is None:
            raise ValueError("no such album")
        if not said.get("criteria"):
            raise ValueError("That album is already plain; there is nothing to freeze.")
        clause, args = scope_where(all_of(
            Scope(queries.IN_LIBRARY), criteria.resolve(self.conn, set_id)))
        held = [str(row[0]) for row in self.conn.execute(
            f"SELECT DISTINCT i.content_hash FROM images i WHERE {clause}"
            " AND i.content_hash IS NOT NULL", args)]
        if held:
            sets.add(self.conn, set_id, held)
        sets.redefine(self.conn, set_id, None)
        self.conn.commit()
        # The rules ride back so the freeze can be taken back: redefine
        # with them and the album is smart again, its frozen members pinned
        # in past the rules as any dragged-in photograph is.
        return {"frozen": len(held), "criteria": said["criteria"]}

    def redefine_album(self, set_id: str, criteria) -> dict:
        """Give an album rules again -- the way back from a freeze."""

        self._open()
        said = sets.redefine(self.conn, set_id, criteria)
        if said is None:
            raise ValueError("no such album")
        self.conn.commit()
        return said

    def save_view(self, name: str, view: dict | None) -> dict:
        """The current view, kept: folder and album fold into chips, so
        what you saved is exactly what you were looking at, live."""

        view = dict(view or {})
        chips = list(view.get("chips") or [])
        held = [str(f) for f in (view.get("folders") or ()) if str(f)]
        if held:
            chips.append({"is": "folder", "values": held})
        if view.get("album"):
            chips.append({"is": "in", "values": [str(view["album"])]})
        if not chips:
            raise ValueError("this view is the whole library; narrow it first")
        return self.create_album(name, chips)

    def save_photos(self, name: str, photo_ids) -> dict:
        """These exact photographs, kept: a plain album from a moment --
        a search's results, a hand selection."""

        self._open()
        identities = self._identities(photo_ids)
        set_id = sets.create(self.conn, name)
        sets.add(self.conn, set_id, identities)
        self.conn.commit()
        return {"id": set_id, "name": str(name).strip(), "kept": len(identities)}

    def cameras(self) -> list[dict]:
        self._open()
        return cameras_of(self.conn)

    def facets(self) -> dict:
        """The made answer; made here only the first time. A stamp that
        moved asks the sweep lane for a fresh one and answers with the last
        meanwhile, so the window never waits a second for its offers."""

        self._open()
        if self._facets is None:
            self._facets = (shape_stamp(self.conn), facets_of(self.conn))
            return self._facets[1]
        if self._facets[0] != shape_stamp(self.conn):
            self._refacet()
        return self._facets[1]

    def _sample_tiles(self, scope) -> list[dict]:
        rows = self._with_urls(queries.photos(
            self.conn, scope=scope, sort="best", limit=3,
            renditions=self.tiles.renditions, reachable_on=self._here()))
        return [{"tile": row["tile"]} for row in rows if row.get("tile")]

    def _face_samples(self, sample) -> list[dict]:
        """Face crops as {tile, view}: the stored tile plus a CSS view box
        framing the face. The crop is presentation — the box was always the
        data, and no second picture is ever rendered."""

        hashes = [entry["hash"] for entry in sample]
        if not hashes:
            return []
        marks = ",".join("?" for _ in hashes)
        rows = self._with_urls(queries.photos(
            self.conn, scope=Scope(f"i.content_hash IN ({marks})", tuple(hashes)),
            sort="newest", limit=len(hashes), renditions=self.tiles.renditions,
            reachable_on=self._here()))
        tiles = {row["hash"]: row["tile"] for row in rows if row.get("tile")}

        def pc(v: float) -> float:
            return round(max(0.0, min(1.0, v)) * 100, 2)

        out = []
        for entry in sample:
            tile = tiles.get(entry["hash"])
            if not tile:
                continue
            x, y, w, h = entry["box"]
            side = max(w, h) * 1.9
            cx, cy = x + w / 2, y + h / 2 - h * 0.08  # a breath above centre: eyes
            view = (f"inset({pc(cy - side / 2)}% {pc(1 - (cx + side / 2))}%"
                    f" {pc(1 - (cy + side / 2))}% {pc(cx - side / 2)}%)")
            out.append({"tile": tile, "view": view, "hash": entry["hash"], "box": list(entry["box"])})
        return out

    def people(self) -> list[dict]:
        """Everyone the library can tell apart: the introduced by name, the
        rest as Someones — each with their face, cropped from a tile by the
        box the worker found it in, and each browsable by their tag, because
        seeing someone's photographs is how you decide who they are."""

        import people as persons

        self._open()
        out = []
        # Named first, the biggest first, alphabetical as the tiebreak -- the
        # same rule the labels shelf keeps, so a row stays where the hand
        # learned it between sessions.
        for group in sorted(persons.groups(self.conn),
                            key=lambda g: (not g.get("settled"), -int(g.get("photos") or 0), str(g["name"]).casefold())):
            out.append({
                "term": group["name"], "count": group["photos"],
                "settled": bool(group.get("settled")),
                # The exemplar rides along for everyone: naming a Someone and
                # renaming the named are the same decision on the same face.
                "person": group["exemplar"],
                "samples": self._face_samples(group["sample"]),
            })
        return out

    def labels(self) -> list[dict]:
        """Every word the owner has taught: tilde-counted, with its three
        best-ranked tiles. The vocabulary is exactly what has been answered
        for — an untaught library has no labels at all."""

        import labels as taught
        from model.scope import label as worn

        self._open()
        out = []
        for entry in taught.vocabulary(self.conn):
            clause, args = scope_where(all_of(
                Scope(queries.IN_LIBRARY), worn([entry["name"]])))
            count = int(self.conn.execute(
                f"SELECT COUNT(*) FROM images i WHERE {clause}",
                args).fetchone()[0])
            out.append({"term": entry["name"], "count": count,
                        "samples": self._sample_tiles(worn([entry["name"]]))})
        out.sort(key=lambda entry: entry["count"], reverse=True)
        return out

    def rename_label(self, word: str, called: str) -> dict:
        """Call a taught word something else. The teaching stays: the set
        keeps its members and its answers under the new name."""

        import labels as taught

        self._open()
        set_id = taught._find(self.conn, word)
        if set_id is None:
            raise ValueError(f"no word called {word!r}")
        if taught._find(self.conn, called) not in (None, set_id):
            raise ValueError(f"there is already a word called {called.strip()!r}")
        said = sets.rename(self.conn, set_id, called)
        self.conn.commit()
        return {"id": set_id, "word": said["name"] if said else called}

    def forget_label(self, word: str) -> dict:
        """Stop offering a taught word. The way back is remember_album on
        the id returned: a label is a set, and a forgotten set can be
        remembered."""

        import labels as taught

        self._open()
        set_id = taught._find(self.conn, word)
        if set_id is None:
            raise ValueError(f"no word called {word!r}")
        sets.forget(self.conn, set_id)
        self.conn.commit()
        return {"id": set_id}

    def teach(self, word: str, photo_ids, yes: bool, space=None) -> dict:
        """One answer about one word: these photographs are (Y) or are not
        (N) what it means. The first answer creates the word.

        The answer recomputes before this returns — the one word's rows
        rewrite against the space the caller already holds — so a view
        filtered to the word can re-ask immediately and see the teaching
        held. The rank lane's whole pass still reconciles behind it."""

        import labels as taught

        self._open()
        said = taught.teach(self.conn, word, self._identities(photo_ids), bool(yes))
        subjects, matrix = space if space is not None else ((), None)
        taught.relabel(self.conn, subjects, matrix, words=[said["word"]])
        return said

    def name_person(self, exemplar: str, called: str) -> dict:
        """Introduce someone: the name lands on the exemplar face and the
        rank lane re-groups around it."""

        import people as persons

        self._open()
        said = persons.name(self.conn, exemplar, called)
        return said

    def maybe_same(self) -> list[dict]:
        """The wall's question: pairs of groups close enough to be one
        person, each side with its face and its name."""

        import people as persons

        self._open()
        by_exemplar = {g["exemplar"]: g for g in persons.groups(self.conn)}
        # Every question's faces in one read, each look keyed by the face
        # it is (the photograph and the box: two people share a frame).
        pairs = [(by_exemplar.get(p["a"]), by_exemplar.get(p["b"]), p["close"])
                 for p in persons.maybe_same(self.conn)]
        pairs = [(a, b, close) for a, b, close in pairs if a and b]
        wanted = [g["sample"][0] for a, b, _ in pairs for g in (a, b) if g["sample"]]
        looks = {(look["hash"], tuple(look["box"])): look for look in self._face_samples(wanted)}

        def side(g):
            sample = g["sample"][:1]
            return {"exemplar": g["exemplar"], "term": g["name"], "settled": bool(g.get("settled")),
                    "samples": [looks[key] for s in sample if (key := (s["hash"], tuple(s["box"]))) in looks]}

        return [{"a": side(a), "b": side(b), "close": close} for a, b, close in pairs]

    def same_people(self, a: str, b: str, called: str) -> dict:
        """The owner's Yes: both groups answer to one name, which is how a
        split heals. A name is needed; with neither side introduced, the
        wall asks for one first."""

        import people as persons

        self._open()
        since = persons.last_word(self.conn)
        persons.name(self.conn, a, called)
        persons.name(self.conn, b, called)
        return {"named": called, "since": since, "until": persons.last_word(self.conn)}

    def unname_since(self, since: int, until: int | None = None) -> dict:
        """The way back from a Yes: every face named in (since, until]
        answers to what it answered to before."""

        import people as persons

        self._open()
        return persons.unname_since(self.conn, int(since), None if until is None else int(until))

    def keep_apart(self, a: str, b: str) -> dict:
        import people as persons

        self._open()
        return persons.keep_apart(self.conn, a, b)

    def unname_person(self, exemplar: str) -> dict:
        """Take a first naming back: the face is a Someone again."""

        import people as persons

        self._open()
        return persons.unname(self.conn, exemplar)

    # ---- rank ----

    def ranking(self, view: dict | None) -> Scope:
        """What Rank draws from: the view you are in, or the library without
        its snapshots -- phone shots rank only when you go to them
        ("Everything, just not by default", 07-31)."""

        looking = self.viewing(view)
        if looking:
            return looking
        # The default draw keeps stacks collapsed too: twenty-six frames of
        # one burst must not flood a round.
        return all_of(outside(intake.ROOTS[intake.SNAPSHOTS]), covers_only())

    def rank(self, n: int = 9, view: dict | None = None, avoid=(),
             mode: str = "learn", space=None) -> dict:
        """A set worth comparing, from what can be shown this instant, and how
        far the scope has been ranked. `mode` picks the ordering — close,
        random, diverse, tournament — never a different question."""

        self._open()
        scope = self.ranking(view)
        chosen = rank.candidates(self.conn, n, scope=all_of(scope, self.tiles.ready),
                                 avoid=avoid, mode=mode, space=space,
                                 unsure=self._owner._unsure if self._owner is not None else None)
        rows = {row["id"]: row for row in self._with_urls(queries.photos(
            self.conn, scope=these([p["id"] for p in chosen]), sort="newest", limit=max(1, len(chosen)),
            offset=0, renditions=self.tiles.renditions, reachable_on=self._here(),
        ))} if chosen else {}
        return {
            "photos": [{**rows[p["id"]], "comparisons": p["comparisons"], "rating": p["rating"]}
                       for p in chosen if p["id"] in rows],
            **rank.progress(self.conn, scope),
            # Rows, not distinct identities: the same photograph filed twice
            # counts twice here, a rounding error in a progress figure, and
            # the distinct count cost 1.7 s of every round at 150k rows.
            "total": queries.size(self.conn, scope),
        }

    def round(self, winner_id: int, over_ids) -> dict:
        self._open()
        return rank.record(self.conn, int(winner_id), over_ids)

    def unround(self, decision: int) -> dict:
        self._open()
        return rank.retract(self.conn, int(decision))

    def forget_missing(self, folder: str = "", dry: bool = False) -> dict:
        """Forget every photograph under a folder (or anywhere) that no drive
        holds -- Lightroom's remove-missing, scoped to the tree. With `dry`
        it only counts, so the window can say what one click would do."""

        self._open()
        clause, args = scope_where(in_folder(folder) if folder else EVERYTHING)
        missing = (f"SELECT i.id FROM images i WHERE {queries.IN_LIBRARY} AND ({clause})"
                   f" AND NOT EXISTS (SELECT 1 FROM copies c WHERE c.photo_id = i.id)")
        if dry:
            count = int(self.conn.execute(f"SELECT COUNT(*) FROM ({missing})", args).fetchone()[0])
            return {"forgotten": count, "dry": True}
        cursor = self.conn.execute(f"DELETE FROM images WHERE id IN ({missing})", args)
        self.conn.commit()
        return self.changed({"forgotten": int(cursor.rowcount)})

    def forget(self, photo_ids) -> dict:
        """Drop rows that no drive holds. A row is an address; its decisions
        live under the identity and come back with the file if it ever does."""

        self._open()
        wanted = sorted({int(i) for i in photo_ids if int(i) > 0})
        if not wanted:
            return {"forgotten": 0, "kept": []}
        marks = ",".join("?" * len(wanted))
        held = {
            int(row[0]) for row in self.conn.execute(
                f"SELECT DISTINCT photo_id FROM copies WHERE photo_id IN ({marks})", wanted)
        }
        gone = [i for i in wanted if i not in held]
        if gone:
            self.conn.execute(
                f"DELETE FROM images WHERE id IN ({','.join('?' * len(gone))})", gone)
            self.conn.commit()
        return {"forgotten": len(gone), "kept": sorted(held)}

    def trash_count(self) -> int:
        self._open()
        return trash.count(self.conn)

    def browse_trash(self, *, limit: int = 200, offset: int = 0) -> list[dict]:
        self._open()
        return self._with_urls(queries.trash(
            self.conn, limit=limit, offset=offset, renditions=self.tiles.renditions,
            reachable_on=self._here(),
        ))

    def empty_trash(self, expected_count: int, *, dry_run: bool = False) -> dict:
        self._open()
        said = trash.empty(
            self.conn,
            expected_count=int(expected_count),
            dry_run=bool(dry_run),
        )
        return said if dry_run else self.changed(said)

    def faces(self, photo_id: int) -> list[list[float]]:
        """The faces found on one photograph, as boxes in fractions of the
        picture (x, y, w, h) -- what the loupe zooms to for a sharpness
        read on the eyes. Empty until the face pass has been there."""

        import faces as facing

        self._open()
        row = self._photo_row(photo_id)
        if row is None or not row["hash"]:
            return []
        held = cache.get(self.conn, row["hash"], self.looking_at_people, {"model": facing.KEY})
        if held is None or held.get("state") != cache.READY or not held.get("value"):
            return []
        boxes, _scores, _matrix = facing.unpack(held["value"])
        return [[float(v) for v in box] for box in boxes]

    def details(self, photo_id: int) -> dict | None:
        """Return and project embedded browse metadata for one photograph."""

        self._open()
        found = self._source_identity(photo_id)
        if found is None:
            # The original is away: what the cache read from it when it was
            # here still answers, and so do the facts the tiles derived.
            row = self.conn.execute(
                "SELECT content_hash AS hash FROM images WHERE id = ?", (int(photo_id),)).fetchone()
            digest = row["hash"] if row else None
            entry = cache.get(self.conn, digest, embedded_metadata.KIND) if digest else None
            if entry is None or entry.get("state") != cache.READY:
                return None
        else:
            source, digest = found
            entry = cache.make(self.conn, digest, embedded_metadata.KIND, source)
            if entry is None:
                return None
        if not cache.project(
            self.conn, digest, photo_id, embedded_metadata.KIND, entry
        ):
            return None
        answer = embedded_metadata.decoded(entry)
        # A place decided (from a track, or one day by hand) outranks the
        # file's own; the file's stands where nothing was ever decided.
        placed = places.of(self.conn, digest)
        if placed:
            answer["lat"] = placed.get("lat")
            answer["lon"] = placed.get("lon")
        # How this photograph's score is known: the rounds it was actually
        # in. Zero with a moved score means the ranking predicted it.
        answer["rounds"] = rank.seen(self.conn).get(digest, 0)
        # Where the sharpness sits, when the pass has been there: the two
        # facts the inspector says; the whole record stays for the fit.
        held = sharpness.of(self.conn, digest)
        answer["sharp"] = {"subject": held.get("subject"), "eyes": held.get("eyes")} if held else None
        # A sweep this frame is part of: what S would stack, and the merge to
        # come. Judged once per run and kept, so the ask is a read after that.
        import panorama

        answer["sweep"] = panorama.of(self.conn, self.tiles, int(photo_id))
        # Every name this photograph wears — palette tags, groups the space
        # formed, people — so the panel can answer "why is this here".
        import json as coding

        names: set[str] = set()
        for row in self.conn.execute(
            "SELECT value FROM cache WHERE kind IN ('alike', 'people')"
            " AND state = 'ready' AND hash = ?", (digest,)):
            held = row["value"]
            try:
                names.update(coding.loads(held if isinstance(held, str) else bytes(held).decode("utf-8")))
            except (ValueError, TypeError):
                continue
        answer["names"] = sorted(names)
        return answer

    def set_date(self, photo_id: int, value: str) -> str:
        """Correct one capture date as an owner decision and reproject it."""

        self._open()
        normalized = tags.normalize_date(value)
        if normalized is None:
            raise ValueError(f"invalid capture date: {value!r}")
        found = self._source_identity(photo_id)
        if found is None:
            raise ValueError(f"no available photo: {photo_id}")
        _source, digest = found
        decisions.decide(self.conn, digest, decisions.DATE, normalized)
        entry = cache.get(self.conn, digest, embedded_metadata.KIND)
        if entry is not None and entry["state"] == cache.READY:
            if cache.project(self.conn, digest, photo_id, embedded_metadata.KIND, entry):
                return normalized
        cache.forget(self.conn, digest, kind=embedded_metadata.KIND)
        self.conn.commit()
        if self.details(photo_id) is None:
            raise RuntimeError("capture date was saved but metadata could not be reprojected")
        return normalized

    def debt(self) -> dict[str, int]:
        self._open()
        return work.debt(self.conn, (embedded_metadata.KIND, *self.tiles.kinds, self.space))

    def pulse(self) -> dict[str, int]:
        """What a window asks every couple of seconds: did anything land?

        No query, so asking costs nothing: what the worker finished, how many
        sweeps of the folders have completed, the kind it is on, and what it
        counted as still owed. The window already holds the counts it would
        want next; a moved pulse is its cue to re-read them. `debt` is the
        expensive full accounting, asked directly only by tests.
        """

        return {"done": self.chores.done, "swept": self.swept,
                "doing": self.chores.doing, "left": self.chores.left}

    def _source_identity(self, photo_id: int) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT content_hash AS hash FROM images WHERE id = ?", (int(photo_id),)
        ).fetchone()
        if row is None:
            return None
        source = photos.open_photo(self.conn, photo_id)
        if source is None:
            return None
        digest = row["hash"]
        if digest is None:
            digest = photos.content_hash(source)
            self.conn.execute(
                "UPDATE images SET content_hash = ? WHERE id = ? AND content_hash IS NULL",
                (digest, int(photo_id)),
            )
            self.conn.commit()
        return source, digest

    def _open(self) -> None:
        if self._closed:
            raise RuntimeError("library is closed")


class OwnedLibrary:
    """Keep one ``Library`` and all access to its connection on one thread."""

    def __init__(self, catalog_path: str, tile_root: str):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="library")
        self._scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sweep")
        # Derivations (the ranking, people, labels) have their own lane: a
        # round's rerank must not queue behind a minute's walk of the archive.
        self._derive_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="derive")
        self._state = threading.Lock()
        self._closed = False
        self._close_future = None
        self._executor_shutdown = False
        self._stop_following = threading.Event()
        self._follower: threading.Thread | None = None
        self._ranking = False
        self._warming = False
        self._ranked = None            # (last round row, vector count) already written
        self._unsure: dict[str, float] = {}   # the fit's own uncertainty, for Learn's draws
        self._space = None             # (vector count, subjects, matrix), append-only so count-keyed
        self._peopled = None           # (face rows, last person decision) already grouped
        self._labeled = None           # (vector count, last teaching) already written
        # Bumped whenever the lane rewrites a derived answer the window shows
        # (clusters, people); rides the pulse so the window knows to re-ask.
        self.shaped = 0
        # Bringing photographs in has its own lane: a card takes minutes, and
        # neither browsing nor the minute sweep may wait behind it.
        self._intake_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intake")
        # Staged thumbnails on their own pair of workers: bounded, and never
        # behind an import running on the intake lane.
        self._thumb_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumb")
        self._intake = {"phase": "idle"}
        self._intake_stop = threading.Event()
        self._staged: dict[str, list[dict]] = {}
        self.cards: list[dict] = []
        try:
            self._library = self._executor.submit(Library, catalog_path, tile_root).result()
            self._library._owner = self
        except BaseException:
            self._derive_executor.shutdown(wait=True, cancel_futures=True)
            self._scan_executor.shutdown(wait=True, cancel_futures=True)
            self._executor.shutdown(wait=True, cancel_futures=True)
            raise

    async def run(self, operation: Callable[[Library], Result]) -> Result:
        """Run one product operation without moving its SQLite connection."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._executor.submit(operation, self._library)
        return await asyncio.wrap_future(future)

    async def refresh(self, drive_uuid: str, under: str = "") -> dict:
        """Sweep on its own connection so the library remains browseable."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._scan_executor.submit(self._sweep, drive_uuid, under)
        return await asyncio.wrap_future(future)

    async def find(self, query: str, limit: int = 200, offset: int = 0,
                   view: dict | None = None, like=()) -> dict:
        """Search, with whatever this machine has warm.

        The space comes from the rank lane's memo; the query becomes a vector
        only when the model is already in memory. Cold but capable means
        words-only now and a warm-up on the sweep lane, so the next search is
        semantic -- the typing path never waits for a model.

        A `like` search seeds from photographs already in the space, so it
        needs no model at all -- only the memo, which the first rank pass
        after boot fills.
        """

        import embed

        space = self.spaced()
        if space is None:
            self.rank_soon()
        query_vector = None
        if not like:
            if embed.warm():
                query_vector = embed.text(query)
            elif embed.ready():
                with self._state:
                    if not self._warming:
                        self._warming = True
                        self._scan_executor.submit(embed._model)
        return await self.run(lambda library: library.search(
            query, limit=int(limit), offset=int(offset), space=space,
            query_vector=query_vector, view=view, like=like,
        ))

    def spaced(self):
        """The memoized space as (subjects, matrix), or None before the
        first rank pass has read it."""

        if self._space is None:
            return None
        return (self._space[1], self._space[2])

    def rank_soon(self) -> None:
        """Recompute the ranking behind the sort index, off the interactive
        lane, at most once for any burst of rounds."""

        with self._state:
            if self._closed or self._ranking:
                return
            self._ranking = True
            self._derive_executor.submit(self._rank)

    def _rank(self) -> None:
        with self._state:
            self._ranking = False
        conn = model.connect(self._library.catalog_path)
        try:
            import embed
            import faces as facing
            import people as persons

            # Everything this lane derives, in one change key: rounds and
            # vectors move the ranking, faces and introductions move the
            # people. Any of the four waking up wakes the whole pass — the
            # sub-steps still skip on their own narrower keys.
            key = (
                conn.execute(
                    "SELECT MAX(id) FROM decisions WHERE family = ?", (decisions.COMPARE,)
                ).fetchone()[0],
                conn.execute(
                    "SELECT COUNT(*) FROM cache WHERE kind = 'embedding' AND recipe = ? AND state = 'ready'",
                    (embed.RECIPE,),
                ).fetchone()[0],
                conn.execute(
                    "SELECT COUNT(*) FROM cache WHERE kind = 'faces' AND recipe = ? AND state = 'ready'",
                    (facing.RECIPE,),
                ).fetchone()[0],
                conn.execute(
                    "SELECT MAX(id) FROM decisions WHERE family IN (?, ?)", (persons.FAMILY, persons.APART)
                ).fetchone()[0],
            )
            if key == self._ranked:
                return
            # People first: a name or a Yes is answered in the time the
            # groups take to rewrite (under a second), and the window hears
            # it at once -- not after the whole space has been reranked.
            # Needs no model: the vectors are already in the rows.
            if key[2:] != self._peopled and key[2]:
                try:
                    persons.repeople(conn)
                except Exception:  # noqa: BLE001 -- the ranking must not wait on the faces
                    log.exception("the people could not be rewritten")
                self._peopled = key[2:]
                self.shaped += 1
            # The matrix is hundreds of megabytes on a full library and the
            # rows are append-only, so it is re-read only when the count
            # moved; a sitting of rounds reranks against the space in memory,
            # and only when the rounds or the space moved. The window hears
            # of it separately: the grid's order is the rerank's.
            if self._space is None or self._space[0] != key[1]:
                self._space = (key[1], *rank.space(conn))
            _count, subjects, vectors = self._space
            if self._ranked is None or key[:2] != self._ranked[:2]:
                scores, self._unsure = rank.fitted(rank.rounds(conn), subjects, vectors)
                queries.rerank(conn, scores=scores)
                self.shaped += 1
            self._ranked = key
            # Labels too: when a word was taught or the space grew, every
            # taught word's answer rewrites whole.
            import labels as taught

            families = [sets.family(entry["id"]) for entry in taught.vocabulary(conn)]
            if families:
                marks = ",".join("?" * len(families))
                latest = conn.execute(
                    f"SELECT MAX(id) FROM decisions WHERE family IN ({marks})", families
                ).fetchone()[0]
                if (key[1], latest) != self._labeled:
                    taught.relabel(conn, subjects, vectors)
                    self._labeled = (key[1], latest)
                    self.shaped += 1
        finally:
            conn.close()

    def submit_sweep(self, work_item):
        """Run something on the sweep lane -- the library's own long reads."""

        return self._scan_executor.submit(work_item)

    def _sweep(self, drive_uuid: str, under: str = "") -> dict:
        conn = model.connect(self._library.catalog_path)
        try:
            said = copies.sweep(conn, drive_uuid, under=under)
            # The walk noticed Lightroom's sidecars and rewrites; their
            # settings become decisions here, on the sweep's own lane.
            root = drives.root_of(conn, drive_uuid)
            if said.get("applied") and root:
                said["edits_adopted"] = developing.adopt(
                    conn, root, said["sidecars"], said.get("changed") or ())
                stacks.project(conn)
            # A sweep that found nothing new is not news: the window re-reads
            # its shelves, folders and chapters on `swept`, and a quiet
            # minute must not cost that.
            if any(said.get(k) for k in ("photos_added", "photos_moved", "copies_retired", "edits_adopted")):
                self._library._reshape(conn, self._library.swept + 1)
                self._library.swept += 1
            return said
        finally:
            conn.close()
            self._library.chores.nudge()

    def _repair(self) -> None:
        conn = model.connect(self._library.catalog_path)
        try:
            repair(conn)
        finally:
            conn.close()
            # The repair may have moved columns the window is showing
            # (stacks, dates); it re-reads once the repair lands.
            self._library.changed(None)

    async def export_files(self, photo_ids, destination: str, *, quality: int = 92,
                           long_edge: int = 0, rename: str = "") -> dict:
        """JPEGs of the chosen photographs, rendered into one folder of the
        owner's choosing — the edit applied, the turn honoured, the capture
        facts carried. `long_edge` bounds the size (0 is full), `rename`
        gives every file one name and a chronological sequence."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._scan_executor.submit(
                self._export_files, [int(i) for i in photo_ids], str(destination),
                int(quality), int(long_edge), str(rename or "").strip())
        return await asyncio.wrap_future(future)

    def _export_files(self, photo_ids, destination: str, quality: int,
                      long_edge: int, rename: str) -> dict:
        import exports

        conn = model.connect(self._library.catalog_path)
        try:
            os.makedirs(destination, exist_ok=True)
            if rename:
                # The sequence walks capture order, so name-003 was taken
                # after name-002 no matter how the selection was gathered.
                marks = ",".join("?" * len(photo_ids))
                photo_ids = [row["id"] for row in conn.execute(
                    f"SELECT id FROM images WHERE id IN ({marks})"
                    " ORDER BY date_taken ASC, id ASC", photo_ids)]
            wide = max(3, len(str(len(photo_ids))))
            tally = {"exported": 0, "missing": 0, "failed": 0}
            for number, photo_id in enumerate(photo_ids, start=1):
                stem = f"{rename}-{number:0{wide}d}" if rename else None
                tally[exports.jpeg(conn, photo_id, destination, quality=quality,
                                   long_edge=long_edge, stem=stem)] += 1
            return {**tally, "destination": destination}
        finally:
            conn.close()

    async def adopt_track(self, path: str, offset_hours: float | None = None) -> dict:
        """Read one GPX file and give every dated photograph inside its span
        a place — the phone knew where, the camera knew when."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._scan_executor.submit(self._adopt_track, str(path), offset_hours)
        return await asyncio.wrap_future(future)

    def _adopt_track(self, path: str, offset_hours: float | None) -> dict:
        conn = model.connect(self._library.catalog_path)
        try:
            offset = None if offset_hours is None else float(offset_hours) * 3600.0
            return {"placed": places.adopt_track(conn, path, offset_seconds=offset)}
        finally:
            conn.close()

    async def synchronize(self, folder: str = "") -> list[dict]:
        """Sweep one folder (or everything) on every drive that is here now --
        the tree's *Synchronize folder*, and what the following loop does."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._scan_executor.submit(self._synchronize, folder, False)
        return await asyncio.wrap_future(future)

    def _synchronize(self, folder: str = "", working_only: bool = False) -> list[dict]:
        conn = model.connect(self._library.catalog_path)
        try:
            rows = conn.execute("SELECT uuid, is_record FROM drives ORDER BY id").fetchall()
            wanted = [
                str(row["uuid"]) for row in rows
                if (not working_only or not row["is_record"]) and drives.online(conn, row["uuid"])
            ]
        finally:
            conn.close()
        return [self._sweep(uuid, folder) for uuid in wanted]

    # ---- bringing photographs in ----

    async def stage(self, source: str) -> dict:
        """Look at a source and say what is there and where it would go."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._intake_executor.submit(self._stage, source)
        return await asyncio.wrap_future(future)

    def _stage(self, source: str) -> dict:
        source = os.path.abspath(source)
        # The staging pictures are for this look only; the last look's go.
        import shutil

        shutil.rmtree(os.path.join(self._library.tiles.root, ".staging"), ignore_errors=True)

        def looked(count: int) -> None:
            # A card of thousands reads for half a minute; a count is the
            # difference between "looking" and "looks dead". Never over a
            # running import's own words.
            with self._state:
                if self._intake.get("phase") in ("idle", "staging"):
                    self._intake = {"phase": "staging", "source": source, "seen": count}

        conn = model.connect(self._library.catalog_path)
        try:
            candidates = intake.scan(conn, source, progress=looked)
            receiving = drives.receiving(conn)
        finally:
            conn.close()
            with self._state:
                if self._intake.get("phase") == "staging":
                    self._intake = {"phase": "idle"}
        self._staged[source] = candidates
        guess = intake.guess_kind(source, (c["name"] for c in candidates))
        if guess is None and "takeout" in source.lower():
            guess = intake.SNAPSHOTS
        return {
            "source": source,
            "kind": guess,
            "rolls": intake.rolls(candidates),
            "roots": dict(intake.ROOTS),
            "receiving": receiving["label"] if receiving else None,
            "candidates": [{k: v for k, v in c.items() if k != "path"} for c in candidates],
        }

    async def bring(self, source: str, keys: list[str], kind: str, *, clear_source: bool = False,
                    roll: str = "", rolls: dict[str, str] | None = None, include_culled: bool = False) -> dict:
        """Start bringing the chosen staged photographs in, on the intake lane."""

        source = os.path.abspath(source)
        staged = self._staged.get(source)
        if staged is None:
            raise ValueError("stage the source first")
        wanted = {str(k) for k in keys}
        chosen = [c for c in staged if c["key"] in wanted]
        conn = model.connect(self._library.catalog_path)
        try:
            receiving = drives.receiving(conn)
        finally:
            conn.close()
        if receiving is None:
            raise ValueError("No drive is here to receive photographs. Attach a folder first.")
        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            if self._intake.get("phase") == "bringing":
                raise ValueError("an import is already running")
            self._intake_stop.clear()
            self._intake = {"phase": "bringing", "source": source, "kind": kind, "done": 0,
                            "total": len(chosen), "brought": 0, "already": 0, "skipped": 0,
                            "cleared": 0, "failed": 0, "culled": 0, "bytes": 0, "started": time.time()}
            self._intake_executor.submit(self._bring, source, chosen, kind, receiving["uuid"],
                                         bool(clear_source), str(roll or ""), dict(rolls or {}), bool(include_culled))
        return dict(self._intake)

    def _bring(self, source, chosen, kind, drive_uuid, clear_source, roll, rolls, include_culled=False) -> None:
        conn = model.connect(self._library.catalog_path)

        def progress(tally: dict) -> None:
            self._intake.update({k: tally[k] for k in
                                 ("done", "total", "brought", "already", "skipped", "cleared", "failed", "bytes")})
            self._intake["elapsed"] = time.time() - self._intake["started"]
            self._library.chores.nudge()

        try:
            tally = intake.bring(conn, drive_uuid, kind, chosen, roll=roll, rolls_by_group=rolls,
                                 clear_source=clear_source, include_culled=include_culled,
                                 progress=progress, stop=self._intake_stop.is_set)
            # What just came in is the pinned Previous import, rolled whole:
            # the last import's members leave, this one's arrive.
            if tally.get("hashes"):
                if sets.describe(conn, Library.LAST_IMPORT) is None:
                    sets.create(conn, Library.PINNED[Library.LAST_IMPORT], set_id=Library.LAST_IMPORT)
                standing = sets.members(conn, Library.LAST_IMPORT)
                if standing:
                    sets.remove(conn, Library.LAST_IMPORT, standing)
                sets.add(conn, Library.LAST_IMPORT, tally["hashes"])
                conn.commit()
            self._intake.update({k: tally[k] for k in
                                 ("done", "total", "brought", "already", "skipped", "cleared", "failed", "culled", "bytes")})
            self._intake["culled_keys"] = list(tally["culled_keys"])
            self._intake["phase"] = "stopped" if tally["stopped"] else "done"
            self._intake["failures"] = [o for o in tally["outcomes"] if o["outcome"] not in
                                        ("written", "already there", "already in the library", "culled before")][:50]
        except Exception as error:  # noqa: BLE001 - the status carries it
            self._intake["phase"] = "failed"
            self._intake["error"] = str(error)
        finally:
            conn.close()
            self._library.swept += 1
            self._library.chores.nudge()

    def intake_status(self) -> dict:
        return dict(self._intake)

    def stop_intake(self) -> None:
        self._intake_stop.set()

    async def thumb(self, source: str, key: str) -> str | None:
        """A small picture of one staged file -- a raw's embedded preview --
        made on the thumbnail pool (two workers, so a card's worth of asks is
        bounded and never queues behind an import), rendered into the home's
        staging corner so the window can read it; None when the file will
        not render."""

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._thumb_executor.submit(self._thumb, source, key)
        return await asyncio.wrap_future(future)

    def _thumb(self, source: str, key: str) -> str | None:
        if self._closed:
            return None
        import hashlib

        import render

        staged = self._staged.get(os.path.abspath(source)) or []
        candidate = next((c for c in staged if c["key"] == key), None)
        if candidate is None:
            return None
        folder = os.path.join(self._library.tiles.root, ".staging")
        os.makedirs(folder, exist_ok=True)
        name = hashlib.blake2b(f"{candidate['path']}|{candidate['size']}".encode("utf-8"), digest_size=16).hexdigest()
        target = os.path.join(folder, f"{name}.jpg")
        if not os.path.isfile(target):
            try:
                body = render.render(candidate["path"], 320)
            except Exception:  # noqa: BLE001 - a file that will not render shows no picture
                return None
            with open(target + ".part", "wb") as handle:
                handle.write(body)
            os.replace(target + ".part", target)
        return Path(target).as_uri()

    def follow(self, every: float = 60.0) -> None:
        """Keep the catalog true to the folders: every working drive that is
        here is swept on the sweep lane every `every` seconds (measured 0.5 s
        for 1,874 files), and every drive once now, so other programs may add,
        move and cull files and the library keeps up. The archive is swept on
        attach, on request, and at each start; a walk of 144,000 files is not
        something to do every minute."""

        def loop() -> None:
            first = True
            passes = 0
            sweeping = None
            while not self._stop_following.wait(0 if first else 5.0):
                # A card that arrives is noticed within seconds, and so is a
                # drive: the attached list is looked at here, once a pass,
                # and every page read answers from it. A drive that came or
                # went is news the way a changed sweep is.
                try:
                    self.cards = intake.cards()
                except Exception:  # noqa: BLE001
                    self.cards = []
                conn = model.connect(self._library.catalog_path)
                try:
                    here = attached_now(conn)
                finally:
                    conn.close()
                if here != self._library.here:
                    self._library.here = here
                    self._library.swept += 1
                passes += 1
                if not first and (passes % max(1, int(every // 5))):
                    continue
                # The folders are swept every `every` seconds, and the
                # ranking follows the library on the same rhythm: a round
                # reranks at once through its own verb, but the vectors the
                # worker makes reach Best by the minute, because each pass
                # that sees the count move re-reads the whole space.
                with self._state:
                    if self._closed:
                        return
                    if first:
                        self._scan_executor.submit(self._repair)
                    # One sweep in flight at a time, never awaited here: a
                    # card inserted or the archive coming back is noticed
                    # on the next pass even while the first archive walk
                    # (minutes on a USB disk) is still running.
                    if sweeping is None or sweeping.done():
                        sweeping = self._scan_executor.submit(self._synchronize, "", not first)
                        sweeping.add_done_callback(lambda _f: self.rank_soon())
                first = False

        self._stop_following = threading.Event()
        self._follower = threading.Thread(target=loop, name="follow", daemon=True)
        self._follower.start()

    async def close(self) -> None:
        """Drain earlier operations, close the product, and release its thread."""

        with self._state:
            if self._close_future is None:
                self._closed = True
                self._stop_following.set()

                def finish() -> None:
                    self._intake_stop.set()
                    # The follower opens its own connection each pass; the
                    # catalog is released only once it has stopped.
                    if self._follower is not None:
                        self._follower.join(timeout=15.0)
                    self._thumb_executor.shutdown(wait=False, cancel_futures=True)
                    self._intake_executor.shutdown(wait=True, cancel_futures=False)
                    self._scan_executor.shutdown(wait=True, cancel_futures=False)
                    self._derive_executor.shutdown(wait=True, cancel_futures=False)
                    self._library.close()

                self._close_future = self._executor.submit(finish)
            future = self._close_future
        try:
            await asyncio.shield(asyncio.wrap_future(future))
        finally:
            with self._state:
                if not self._executor_shutdown:
                    self._executor.shutdown(wait=True, cancel_futures=False)
                    self._executor_shutdown = True
