"""The V2 product boundary: open the library, answer it, close it.

There is no environment bootstrap, router graph, singleton connection, or V1
schema prelude here. The desktop owns one ``Library`` instance and calls these
operations from whichever native surface replaces the current shell.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time
from typing import Callable, TypeVar

import embed
import library as queries
import metadata as embedded_metadata
import model
import rank
import search as finding
import tiles
import work
from model import cache, copies, cull, decisions, drives, intake, photos, trash
from model.scope import EVERYTHING, Scope, all_of, folder as in_folder, ids as these, outside, where as scope_where

Result = TypeVar("Result")
# How many photographs a window may say it is looking at. A viewport holds a
# few dozen; the bound keeps the worker's ORDER BY small.
LOOKING_AT_MOST = 400


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
        self.conn = model.connect(self.catalog_path)
        try:
            embedded_metadata.reindex(self.conn)
        except Exception:
            self.conn.close()
            raise
        # What the window says it is looking at, most recent statement wins.
        # Read by the worker on every step, so the first tiles made are the
        # ones on screen; nothing else about the worker's order changes.
        self._looking: tuple[int, ...] = ()
        # Sweeps of the folders completed since start, for the window's pulse.
        self.swept = 0
        self.chores = work.Chores(
            lambda: model.connect(self.catalog_path),
            (embedded_metadata.KIND, *self.tiles.kinds, self.space),
            on_screen=lambda: self._looking,
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
        return drives.attach(self.conn, root, label=label, is_record=is_record)

    def refresh(self, drive_uuid: str) -> dict:
        self._open()
        try:
            return copies.sweep(self.conn, drive_uuid)
        finally:
            self.chores.nudge()

    def attached(self) -> list[dict]:
        self._open()
        return [
            {**dict(row), "attached": drives.online(self.conn, row["uuid"])}
            for row in self.conn.execute("SELECT * FROM drives ORDER BY id")
        ]

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
        """The drives attached right now, answered by looking at each marker."""

        return [
            int(row["id"]) for row in self.conn.execute("SELECT id, uuid FROM drives")
            if drives.online(self.conn, row["uuid"])
        ]

    def size(self, scope: Scope = EVERYTHING) -> int:
        self._open()
        return queries.size(self.conn, scope)

    def folders(self) -> list[dict]:
        """One tree over every drive, each node with its count and whether
        everything under it has a copy on a record drive."""

        self._open()
        return queries.folder_tree(self.conn)

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

    def pick(self, photo_ids) -> dict:
        self._open()
        return cull.pick(self.conn, photo_ids)

    def clear_pick(self, photo_ids) -> dict:
        self._open()
        return cull.clear(self.conn, photo_ids)

    def reject(self, photo_ids) -> dict:
        self._open()
        return cull.reject(self.conn, photo_ids)

    def restore(self, photo_ids) -> dict:
        self._open()
        return cull.restore(self.conn, photo_ids)

    def undo_cull(self, changes) -> dict:
        self._open()
        return cull.undo(self.conn, changes)

    def turn(self, photo_ids, by: int = 90) -> dict:
        self._open()
        return cull.turn(self.conn, photo_ids, by=int(by))

    def search(self, query: str, *, limit: int = 200, offset: int = 0,
               space=None, query_vector=None) -> dict:
        """Find photographs: the fused ranking, one page of it as rows.

        The whole answer is capped at 500 -- a search whose five hundredth
        result matters is a browse, and the folder tree is better at it.
        """

        self._open()
        ranked = finding.search(self.conn, query, space=space, query_vector=query_vector)
        page = ranked[int(offset):int(offset) + max(1, int(limit))]
        rows = {row["id"]: row for row in queries.photos(
            self.conn, scope=these(page), sort="newest", limit=max(1, len(page)),
            offset=0, renditions=self.tiles.renditions, reachable_on=self._here(),
        )} if page else {}
        return {"total": len(ranked), "photos": [rows[i] for i in page if i in rows]}

    # ---- refine ----

    def refining(self, folder: str = "") -> Scope:
        """What Refine ranks: the folder you are in, or the library without
        its snapshots -- phone shots rank only when you go to them
        ("Everything, just not by default", 07-31)."""

        return in_folder(folder) if folder else outside(intake.ROOTS[intake.SNAPSHOTS])

    def refine(self, n: int = 9, folder: str = "", avoid=()) -> dict:
        """A set worth comparing, from what can be shown this instant, and how
        far the scope has been ranked."""

        self._open()
        scope = self.refining(folder)
        chosen = rank.candidates(self.conn, n, scope=all_of(scope, self.tiles.ready), avoid=avoid)
        rows = {row["id"]: row for row in self._with_urls(queries.photos(
            self.conn, scope=these([p["id"] for p in chosen]), sort="newest", limit=max(1, len(chosen)),
            offset=0, renditions=self.tiles.renditions, reachable_on=self._here(),
        ))} if chosen else {}
        return {
            "photos": [{**rows[p["id"]], "comparisons": p["comparisons"], "rating": p["rating"]}
                       for p in chosen if p["id"] in rows],
            "judged": rank.judged(self.conn, scope),
            "total": queries.size(self.conn, scope),
        }

    def round(self, winner_id: int, over_ids) -> dict:
        self._open()
        return rank.record(self.conn, int(winner_id), over_ids)

    def unround(self, decision: int) -> dict:
        self._open()
        return rank.retract(self.conn, int(decision))

    def forget_missing(self, folder: str = "") -> dict:
        """Forget every photograph under a folder (or anywhere) that no drive
        holds -- Lightroom's remove-missing, scoped to the tree."""

        self._open()
        clause, args = scope_where(in_folder(folder) if folder else EVERYTHING)
        cursor = self.conn.execute(
            f"DELETE FROM images WHERE id IN (SELECT i.id FROM images i WHERE {queries.IN_LIBRARY} AND ({clause})"
            f" AND NOT EXISTS (SELECT 1 FROM copies c WHERE c.photo_id = i.id))", args)
        self.conn.commit()
        return {"forgotten": int(cursor.rowcount)}

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
        return trash.empty(
            self.conn,
            expected_count=int(expected_count),
            dry_run=bool(dry_run),
        )

    def details(self, photo_id: int) -> dict | None:
        """Return and project embedded browse metadata for one photograph."""

        self._open()
        found = self._source_identity(photo_id)
        if found is None:
            return None
        source, digest = found
        entry = cache.make(
            self.conn, digest, embedded_metadata.KIND, source
        )
        if entry is None:
            return None
        if not cache.project(
            self.conn, digest, photo_id, embedded_metadata.KIND, entry
        ):
            return None
        return embedded_metadata.decoded(entry)

    def set_date(self, photo_id: int, value: str) -> str:
        """Correct one capture date as an owner decision and reproject it."""

        self._open()
        normalized = embedded_metadata.normalize_date(value)
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

        Two integers and no query, so asking costs nothing: what the worker
        finished, and how many sweeps of the folders have completed. The window
        already holds the counts it would want next; a moved pulse is its cue
        to re-read them. `debt` is the expensive full accounting.
        """

        return {"done": self.chores.done, "swept": self.swept}

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
        self._state = threading.Lock()
        self._closed = False
        self._close_future = None
        self._executor_shutdown = False
        self._stop_following = threading.Event()
        self._follower: threading.Thread | None = None
        self._ranking = False
        self._warming = False
        self._ranked = None            # (last round row, vector count) already written
        self._space = None             # (vector count, subjects, matrix), append-only so count-keyed
        # Bringing photographs in has its own lane: a card takes minutes, and
        # neither browsing nor the minute sweep may wait behind it.
        self._intake_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intake")
        self._intake = {"phase": "idle"}
        self._intake_stop = threading.Event()
        self._staged: dict[str, list[dict]] = {}
        self.cards: list[dict] = []
        try:
            self._library = self._executor.submit(Library, catalog_path, tile_root).result()
        except BaseException:
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

    async def find(self, query: str, limit: int = 200, offset: int = 0) -> dict:
        """Search, with whatever this machine has warm.

        The space comes from the rank lane's memo; the query becomes a vector
        only when the model is already in memory. Cold but capable means
        words-only now and a warm-up on the sweep lane, so the next search is
        semantic -- the typing path never waits for a model.
        """

        import embed

        space = None
        if self._space is not None:
            space = (self._space[1], self._space[2])
        else:
            self.rank_soon()
        query_vector = None
        if embed.warm():
            query_vector = embed.text(query)
        elif embed.ready():
            with self._state:
                if not self._warming:
                    self._warming = True
                    self._scan_executor.submit(embed._model)
        return await self.run(lambda library: library.search(
            query, limit=int(limit), offset=int(offset), space=space, query_vector=query_vector,
        ))

    def rank_soon(self) -> None:
        """Recompute the ranking behind the sort index, off the interactive
        lane, at most once for any burst of rounds."""

        with self._state:
            if self._closed or self._ranking:
                return
            self._ranking = True
            self._scan_executor.submit(self._rank)

    def _rank(self) -> None:
        with self._state:
            self._ranking = False
        conn = model.connect(self._library.catalog_path)
        try:
            import embed

            key = (
                conn.execute(
                    "SELECT MAX(id) FROM decisions WHERE family = ?", (decisions.COMPARE,)
                ).fetchone()[0],
                conn.execute(
                    "SELECT COUNT(*) FROM cache WHERE kind = 'embedding' AND recipe = ? AND state = 'ready'",
                    (embed.RECIPE,),
                ).fetchone()[0],
            )
            if key == self._ranked:
                return
            # The matrix is hundreds of megabytes on a full library and the
            # rows are append-only, so it is re-read only when the count
            # moved; a sitting of rounds reranks against the space in memory.
            if self._space is None or self._space[0] != key[1]:
                self._space = (key[1], *rank.space(conn))
            _count, subjects, vectors = self._space
            queries.rerank(conn, subjects, vectors)
            self._ranked = key
        finally:
            conn.close()

    def _sweep(self, drive_uuid: str, under: str = "") -> dict:
        conn = model.connect(self._library.catalog_path)
        try:
            return copies.sweep(conn, drive_uuid, under=under)
        finally:
            conn.close()
            self._library.swept += 1
            self._library.chores.nudge()

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
        conn = model.connect(self._library.catalog_path)
        try:
            candidates = intake.scan(conn, source)
            receiving = drives.receiving(conn)
        finally:
            conn.close()
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
                    roll: str = "", rolls: dict[str, str] | None = None) -> dict:
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
                            "cleared": 0, "failed": 0, "bytes": 0, "started": time.time()}
            self._intake_executor.submit(self._bring, source, chosen, kind, receiving["uuid"],
                                         bool(clear_source), str(roll or ""), dict(rolls or {}))
        return dict(self._intake)

    def _bring(self, source, chosen, kind, drive_uuid, clear_source, roll, rolls) -> None:
        conn = model.connect(self._library.catalog_path)

        def progress(tally: dict) -> None:
            self._intake.update({k: tally[k] for k in
                                 ("done", "total", "brought", "already", "skipped", "cleared", "failed", "bytes")})
            self._intake["elapsed"] = time.time() - self._intake["started"]
            self._library.chores.nudge()

        try:
            tally = intake.bring(conn, drive_uuid, kind, chosen, roll=roll, rolls_by_group=rolls,
                                 clear_source=clear_source, progress=progress, stop=self._intake_stop.is_set)
            self._intake.update({k: tally[k] for k in
                                 ("done", "total", "brought", "already", "skipped", "cleared", "failed", "bytes")})
            self._intake["phase"] = "stopped" if tally["stopped"] else "done"
            self._intake["failures"] = [o for o in tally["outcomes"] if o["outcome"] not in
                                        ("written", "already there", "already in the library")][:50]
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

    def thumb(self, source: str, key: str) -> str | None:
        """A small picture of one staged file, rendered into the home's staging
        corner so the window can read it; None when the file will not render."""

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
            while not self._stop_following.wait(0 if first else 5.0):
                # A card that arrives is noticed within seconds; the folders are
                # swept every `every` seconds (each fifth pass of five).
                try:
                    self.cards = intake.cards()
                except Exception:  # noqa: BLE001
                    self.cards = []
                # Ranking follows the library on every pass, not only the
                # minute sweeps: `_rank` skips in two cheap queries when
                # nothing moved, and a vector the worker just made reaches
                # Best and search within seconds instead of a minute.
                self.rank_soon()
                passes += 1
                if not first and (passes % max(1, int(every // 5))):
                    continue
                with self._state:
                    if self._closed:
                        return
                    future = self._scan_executor.submit(self._synchronize, "", not first)
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - a failed sweep is logged by its lane
                    pass
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
                    self._intake_executor.shutdown(wait=True, cancel_futures=False)
                    self._scan_executor.shutdown(wait=True, cancel_futures=False)
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
