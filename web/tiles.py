"""Persistent JPEG answers for the grid and loupe.

A tile is only a cached answer to one question: what do these photograph
bytes look like at this size and rotation? The content hash and recipe name
the answer, so moving or renaming the source cannot invalidate it.

This module owns files. ``model.cache`` owns the rows describing them and
``work`` decides what is owed. There is no adoption path, scheduler, mutable
global directory, or V1 thumbnail vocabulary here.
"""

from __future__ import annotations

import os
import tempfile

import render
from model import cache

DEFAULT_CEILING_BYTES = 20 * 1024**3


class Store:
    """One tile directory and the cache capability that writes into it."""

    def __init__(self, root: str, *, ceiling_bytes: int = DEFAULT_CEILING_BYTES):
        self.root = os.path.abspath(os.fspath(root))
        self.ceiling_bytes = int(ceiling_bytes)
        if self.ceiling_bytes < 0:
            raise ValueError("tile ceiling cannot be negative")
        self.kind = cache.Kind(
            name="tile",
            compute=self._make,
            params=("size", "rotate"),
            ahead=lambda: (
                {"size": render.GRID, "rotate": 0},
                {"size": render.LOUPE, "rotate": 0},
            ),
            cost=0.4,
            remove=self.remove,
        )

    def path(self, digest: str, size: int, rotate: int = 0) -> str:
        """Return the sole name for an answer, refusing ambiguous inputs."""

        digest = str(digest)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("a tile requires a BLAKE2b-256 hex identity")
        size = int(size)
        if size <= 0:
            raise ValueError("tile size must be positive")
        rotate = int(rotate) % 360
        turn = f"r{rotate}" if rotate else ""
        return os.path.join(self.root, digest[:2], f"{digest}-{size}{turn}.jpg")

    def _make(self, source: str, digest: str, *, size: int = render.GRID,
              rotate: int = 0) -> cache.Made:
        body = render.render(source, int(size), rotate=int(rotate))
        target = self.path(digest, size, rotate)
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)

        staging = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".tile-", suffix=".writing", dir=folder, delete=False
            ) as handle:
                staging = handle.name
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # A hard link publishes a complete file without replacing an
                # answer another worker has already published.
                os.link(staging, target)
            except FileExistsError:
                if not _is_exact(target, body):
                    raise FileExistsError(f"different bytes already occupy {target}")
        finally:
            if staging is not None:
                try:
                    os.remove(staging)
                except FileNotFoundError:
                    pass

        return cache.Made(path=target, bytes=len(body))

    def read(self, entry) -> bytes | None:
        """Read a recorded tile; absence means it may be made again."""

        if not entry or not entry.get("path"):
            return None
        path = self._owned(entry["path"])
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def remove(self, path: str) -> None:
        """Remove exactly one file owned by this store."""

        try:
            os.remove(self._owned(path))
        except FileNotFoundError:
            pass

    def purge(self, conn, digest: str) -> int:
        """Remove every tile answer for one photograph."""

        rows = conn.execute(
            "SELECT path FROM cache WHERE hash = ? AND kind = ?",
            (str(digest), self.kind.name),
        ).fetchall()
        for row in rows:
            if row["path"]:
                self.remove(row["path"])
        removed = cache.forget(conn, digest, kind=self.kind)
        conn.commit()
        return removed

    def clear(self, conn) -> dict[str, int]:
        """Remove only files recorded as this store's tiles; never a tree."""

        rows = conn.execute(
            "SELECT path FROM cache WHERE kind = ?", (self.kind.name,)
        ).fetchall()
        removed = missing = 0
        for row in rows:
            if not row["path"]:
                continue
            try:
                os.remove(self._owned(row["path"]))
                removed += 1
            except FileNotFoundError:
                missing += 1
        conn.execute("DELETE FROM cache WHERE kind = ?", (self.kind.name,))
        conn.commit()
        return {"removed": removed, "already_gone": missing, "rows": len(rows)}

    def status(self, conn) -> dict:
        row = conn.execute(
            "SELECT COUNT(*) AS tiles, COALESCE(SUM(bytes), 0) AS bytes,"
            " SUM(state = 'failed') AS failed FROM cache WHERE kind = ?",
            (self.kind.name,),
        ).fetchone()
        return {
            "directory": self.root,
            "tiles": int(row["tiles"] or 0),
            "bytes": int(row["bytes"] or 0),
            "ceiling_bytes": self.ceiling_bytes,
            "unreadable": int(row["failed"] or 0),
        }

    def _owned(self, path: str) -> str:
        path = os.path.abspath(os.fspath(path))
        if os.path.commonpath((self.root, path)) != self.root or path == self.root:
            raise ValueError(f"tile path is outside its store: {path}")
        return path


def _is_exact(path: str, expected: bytes) -> bool:
    try:
        if os.path.getsize(path) != len(expected):
            return False
        with open(path, "rb") as handle:
            for offset in range(0, len(expected), 1024 * 1024):
                if handle.read(1024 * 1024) != expected[offset:offset + 1024 * 1024]:
                    return False
            return handle.read(1) == b""
    except OSError:
        return False
