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
from typing import Callable, TypeVar

import library as queries
import metadata as embedded_metadata
import model
import tiles
import work
from model import cache, copies, cull, decisions, drives, photos, trash
from model.scope import EVERYTHING, Scope, folder as in_folder

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
        self.chores = work.Chores(
            lambda: model.connect(self.catalog_path),
            (embedded_metadata.KIND, *self.tiles.kinds),
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
            renditions=self.tiles.renditions,
        ))

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

    def trash_count(self) -> int:
        self._open()
        return trash.count(self.conn)

    def browse_trash(self, *, limit: int = 200, offset: int = 0) -> list[dict]:
        self._open()
        return self._with_urls(queries.trash(
            self.conn, limit=limit, offset=offset, renditions=self.tiles.renditions,
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
        return work.debt(self.conn, (embedded_metadata.KIND, *self.tiles.kinds))

    def pulse(self) -> dict[str, int]:
        """What a window asks every couple of seconds: did anything land?

        One integer read from the worker and no query, so asking costs nothing.
        The window already holds the counts it would want next; a moved pulse
        is its cue to re-read them. `debt` is the expensive full accounting.
        """

        return {"done": self.chores.done}

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

    async def refresh(self, drive_uuid: str) -> dict:
        """Sweep on its own connection so the library remains browseable."""

        def sweep() -> dict:
            conn = model.connect(self._library.catalog_path)
            try:
                return copies.sweep(conn, drive_uuid)
            finally:
                conn.close()
                self._library.chores.nudge()

        with self._state:
            if self._closed:
                raise RuntimeError("library is closed")
            future = self._scan_executor.submit(sweep)
        return await asyncio.wrap_future(future)

    async def close(self) -> None:
        """Drain earlier operations, close the product, and release its thread."""

        with self._state:
            if self._close_future is None:
                self._closed = True

                def finish() -> None:
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
