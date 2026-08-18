"""The V2 product boundary: open the library, answer it, close it.

There is no environment bootstrap, router graph, singleton connection, or V1
schema prelude here. The desktop owns one ``Library`` instance and calls these
operations from whichever native surface replaces the current shell.
"""

from __future__ import annotations

import os

import library as queries
import model
import render
import tiles
import work
from model import cache, copies, drives, photos
from model.scope import EVERYTHING, Scope

TILE_SIZES = frozenset((render.GRID, render.LOUPE, 3840))
ROTATIONS = frozenset((0, 90, 180, 270))


class Library:
    """One open catalog, its tile store, and its one background worker."""

    def __init__(self, catalog_path: str, tile_root: str):
        self.catalog_path = os.path.abspath(os.fspath(catalog_path))
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        self.tiles = tiles.Store(tile_root)
        self.conn = model.connect(self.catalog_path)
        self.chores = work.Chores(
            lambda: model.connect(self.catalog_path),
            (self.tiles.kind,),
            ceiling_bytes=self.tiles.ceiling_bytes,
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
        """Attach one chosen folder and perform its first truthful sweep."""

        self._open()
        drive = drives.attach(self.conn, root, label=label, is_record=is_record)
        return {"drive": drive, "sweep": copies.sweep(self.conn, drive["uuid"])}

    def refresh(self, drive_uuid: str) -> dict:
        self._open()
        return copies.sweep(self.conn, drive_uuid)

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
        return queries.photos(
            self.conn, scope=scope, sort=sort, limit=limit, offset=offset
        )

    def tile(self, photo_id: int, *, size: int = render.GRID, rotate: int = 0) -> bytes | None:
        """Return one real tile, making it once when it is absent."""

        self._open()
        size, rotate = int(size), int(rotate)
        if size not in TILE_SIZES:
            raise ValueError(f"tile size is one of {sorted(TILE_SIZES)}")
        if rotate not in ROTATIONS:
            raise ValueError(f"rotation is one of {sorted(ROTATIONS)}")

        row = self.conn.execute(
            "SELECT id, content_hash AS hash FROM images WHERE id = ?",
            (int(photo_id),),
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

        recipe = {"size": size, "rotate": rotate}
        entry = cache.get(self.conn, digest, self.tiles.kind, recipe)
        if entry is not None and entry["state"] == cache.FAILED:
            return None
        body = self.tiles.read(entry)
        if body is not None:
            return body
        entry = cache.make(
            self.conn,
            digest,
            self.tiles.kind,
            source,
            recipe,
            remake=entry is not None,
        )
        return self.tiles.read(entry)

    def debt(self) -> dict[str, int]:
        self._open()
        return work.debt(self.conn, (self.tiles.kind,))

    def _open(self) -> None:
        if self._closed:
            raise RuntimeError("library is closed")
