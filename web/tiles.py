"""Persistent JPEG answers for the grid and loupe.

A tile is only a cached answer to one question: what do these photograph
bytes look like at this size? The content hash names the answer, so moving
or renaming the source cannot invalidate it. A turn the owner asks for is
how the window shows the tile, not a different tile; an edit, when Develop
arrives, will be a recipe because it is different pixels.

Two sizes, two kinds, one store. They are two kinds because they differ in
policy, not in pixels: a grid tile is what keeps the library browsable with
the archive away, so it is never evicted and costs ~170 KB; a loupe tile is
what a full window shows, costs ~2 MB, and may be remade if space is wanted.
One kind with the size in its recipe could not say that.

One read, one decode, both sizes. Whichever kind is asked first decodes the
original at loupe size and publishes both files; the other kind finds its file
already there and only records it. When only the loupe exists, the grid tile
is cut from it and the original is not opened at all -- which is what lets
the grid fill with the archive drive unplugged.

This module owns files. ``model.cache`` owns the rows describing them and
``work`` decides what is owed. There is no adoption path, scheduler, mutable
global directory, or V1 thumbnail vocabulary here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile

from PIL import Image

import render
from model import cache
from model.scope import Scope

GRID = "grid"
LOUPE = "loupe"


def _worn(crop) -> str:
    """The filename's word for a crop: nothing for none, a short digest
    of the exact fragment otherwise — a different crop is a different file."""

    if crop is None:
        return ""
    spelled = json.dumps(list(crop), separators=(",", ":"))
    return "-c" + hashlib.blake2b(spelled.encode(), digest_size=5).hexdigest()


class Store:
    """One tile directory and the two cache capabilities that write into it."""

    def __init__(self, root: str, *, ceiling_bytes: int | None = None):
        self.root = os.path.abspath(os.fspath(root))
        os.makedirs(self.root, exist_ok=True)
        # The ceiling bounds evictable answers only -- loupes -- and defaults to
        # half of what the cache disk has free when the store opens, so a
        # machine with a small disk keeps recent loupes and a machine with a
        # large one keeps them all. The owner may lower it; nothing raises it.
        if ceiling_bytes is None:
            ceiling_bytes = shutil.disk_usage(self.root).free // 2
        self.ceiling_bytes = int(ceiling_bytes)
        if self.ceiling_bytes < 0:
            raise ValueError("tile ceiling cannot be negative")
        # `crop` is the develop geometry: an edited photograph's tile is
        # different pixels, so it is a different recipe — keyed on the
        # photo's own develop column, while the plain tile keeps the recipe
        # (and the bytes) it always had, because the embedding and the faces
        # read the plain pixels.
        self.grid = cache.Kind(
            name=GRID,
            compute=self._make_grid,
            cost=0.3,
            params=("crop",),
            keyed=("crop", "i.develop"),
            evictable=False,
            remove=self.remove,
        )
        self.loupe = cache.Kind(
            name=LOUPE,
            compute=self._make_loupe,
            cost=0.5,
            params=("crop",),
            keyed=("crop", "i.develop"),
            remove=self.remove,
        )
        self.kinds = (self.grid, self.loupe)

    @property
    def renditions(self) -> dict[str, tuple[cache.Kind, dict]]:
        """What a photo row carries: the answer each kind has for it."""

        return {"tile": (self.grid, {}), "loupe": (self.loupe, {})}

    @property
    def ready(self) -> Scope:
        """Photographs whose grid tile exists -- what a surface can show the
        instant it asks, with nothing decoded on the way.

        Spelled in literals rather than bound arguments so the same sentence
        serves as a kind's `wants`, which has no argument channel; every value
        is a module constant, so there is nothing to inject.
        """

        return Scope(
            f"EXISTS (SELECT 1 FROM cache t WHERE t.hash = i.content_hash"
            f" AND t.kind = '{GRID}' AND t.recipe = '{{}}' AND t.state = '{cache.READY}')"
        )

    def path(self, digest: str, size: int, crop=None) -> str:
        """Return the sole name for an answer, refusing ambiguous inputs."""

        digest = str(digest)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("a tile requires a BLAKE2b-256 hex identity")
        size = int(size)
        if size <= 0:
            raise ValueError("tile size must be positive")
        return os.path.join(self.root, digest[:2], f"{digest}-{size}{_worn(crop)}.jpg")

    def _make_grid(self, source: str, digest: str, crop=None) -> cache.Made:
        return self._answer(source, digest, render.GRID, crop)

    def _make_loupe(self, source: str, digest: str, crop=None) -> cache.Made:
        return self._answer(source, digest, render.LOUPE, crop)

    def _answer(self, source: str, digest: str, size: int, crop=None) -> cache.Made:
        target = self.path(digest, size, crop)
        if os.path.isfile(target):
            # Published already, as the other size's by-product. The name is the
            # content and the size, so a file at this path is this answer.
            return cache.Made(path=target, bytes=os.path.getsize(target))

        loupe = self.path(digest, render.LOUPE, crop)
        if size < render.LOUPE and os.path.isfile(loupe):
            # The loupe is the cheapest faithful source: the same pixels, and no
            # original to open.
            return self._publish(target, render.render(loupe, size))

        if crop is not None:
            plain = self.path(digest, render.LOUPE)
            if os.path.isfile(plain):
                # A crop of the plain loupe is the same pixels the original
                # would give, without opening the original — which is what
                # makes adopting hundreds of Lightroom crops cost seconds,
                # and an interactive crop feel instant.
                with Image.open(plain) as held:
                    image = render.cut(held.convert("RGB"), crop)
            else:
                image = render.pixels(source, render.LOUPE, crop=crop)
        else:
            image = render.pixels(source, render.LOUPE)

        grid = self.path(digest, render.GRID, crop)
        try:
            made_loupe = self._publish(loupe, render.encode(image))
            made_grid = (
                cache.Made(path=grid, bytes=os.path.getsize(grid)) if os.path.isfile(grid)
                else self._publish(grid, render.encode(render.fit(image, render.GRID)))
            )
        finally:
            image.close()
        return made_grid if size == render.GRID else made_loupe

    def _publish(self, target: str, body: bytes) -> cache.Made:
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

    def present(self, entry) -> str | None:
        """Return the owned path only while the recorded answer exists."""

        if not entry or not entry.get("path"):
            return None
        path = self._owned(entry["path"])
        return path if os.path.isfile(path) else None

    def remove(self, path: str) -> None:
        """Remove exactly one file owned by this store."""

        try:
            os.remove(self._owned(path))
        except FileNotFoundError:
            pass

    def purge(self, conn, digest: str) -> int:
        """Remove every tile answer for one photograph."""

        rows = conn.execute(
            "SELECT path FROM cache WHERE hash = ? AND kind IN (?, ?)",
            (str(digest), GRID, LOUPE),
        ).fetchall()
        for row in rows:
            if row["path"]:
                self.remove(row["path"])
        removed = 0
        for kind in self.kinds:
            removed += cache.forget(conn, digest, kind=kind)
        conn.commit()
        return removed

    def clear(self, conn) -> dict[str, int]:
        """Remove only files recorded as this store's tiles; never a tree."""

        rows = conn.execute(
            "SELECT path FROM cache WHERE kind IN (?, ?)", (GRID, LOUPE)
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
        conn.execute("DELETE FROM cache WHERE kind IN (?, ?)", (GRID, LOUPE))
        conn.commit()
        return {"removed": removed, "already_gone": missing, "rows": len(rows)}

    def status(self, conn) -> dict:
        row = conn.execute(
            "SELECT COUNT(*) AS tiles, COALESCE(SUM(bytes), 0) AS bytes,"
            " SUM(state = 'failed') AS failed FROM cache WHERE kind IN (?, ?)",
            (GRID, LOUPE),
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
