"""Read-once harvest: one original open → thumbs + hash + metadata.

When any bulk or on-demand path must touch an original on the spindle, call
``harvest_original`` instead of separate thumb / hash / metadata passes. Already
present products are skipped. Bulk callers wrap with the HDD governor; interactive
callers pass ``bulk=False`` and stay outside the gate.

Bulk path (2026-07-20): the governor slot covers ONLY the spindle read into RAM.
Demosaic / decode / resize / encode / write run after release so N decode threads
can use idle cores while the next file streams through the single-flight read.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from core import hdd_governor
from features.sync.hashing import (
    HASH_PREFIX_BYTES,
    compute_content_hash_from_prefix,
)


@dataclass
class HarvestResult:
    source_reads: int = 0
    thumbnails_written: int = 0
    originals_written: int = 0
    source_bytes: int = 0
    read_seconds: float = 0.0
    decode_encode_seconds: float = 0.0
    source_read_failures: int = 0
    content_hash: str | None = None
    hash_written: bool = False
    metadata_written: bool = False
    products: list[str] = field(default_factory=list)
    requested_thumb: bytes | None = None

    def as_thumb_metrics(self) -> dict:
        return {
            "source_reads": self.source_reads,
            "thumbnails_written": self.thumbnails_written,
            "source_bytes": self.source_bytes,
            "read_seconds": self.read_seconds,
            "decode_encode_seconds": self.decode_encode_seconds,
            "source_read_failures": self.source_read_failures,
            "originals_written": self.originals_written,
            "hash_written": int(self.hash_written),
            "metadata_written": int(self.metadata_written),
        }


def image_side_needs(
    db_path: str,
    image_id: int,
    *,
    metadata_version: int,
) -> tuple[bool, bool]:
    """Return (need_hash, need_metadata) for one catalog row."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT content_hash, metadata_scanned_at, metadata_version "
            "FROM images WHERE id = ?",
            (int(image_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False, False
    need_hash = not row["content_hash"]
    version = row["metadata_version"]
    need_metadata = (
        row["metadata_scanned_at"] is None
        or version is None
        or int(version) < int(metadata_version)
    )
    return need_hash, need_metadata


def persist_content_hash_sync(db_path: str, image_id: int, digest: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE images SET content_hash = ? "
            "WHERE id = ? AND content_hash IS NULL",
            (digest, int(image_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def persist_metadata_sync(db_path: str, update_tuple: tuple) -> None:
    """Apply one catalog metadata_update_tuple synchronously."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE images SET "
            "date_taken = CASE "
            "WHEN ? = 'exif' AND ? IS NOT NULL THEN ? "
            "WHEN date_taken IS NULL OR date_taken = '' THEN ? "
            "ELSE date_taken END, "
            "date_source = CASE "
            "WHEN ? = 'exif' AND ? IS NOT NULL THEN 'exif' "
            "WHEN (date_taken IS NULL OR date_taken = '') AND ? IS NOT NULL THEN ? "
            "WHEN (date_source IS NULL OR date_source = '') AND date_taken IS NOT NULL "
            "AND date_taken != '' THEN 'exif' "
            "ELSE date_source END, "
            "camera_make = COALESCE(?, camera_make), "
            "camera_model = COALESCE(?, camera_model), "
            "lens = COALESCE(?, lens), "
            "file_ext = COALESCE(?, file_ext), "
            "file_size = COALESCE(?, file_size), "
            "file_modified_at = COALESCE(?, file_modified_at), "
            "width = COALESCE(?, width), "
            "height = COALESCE(?, height), "
            "metadata_scanned_at = ?, metadata_version = ?, "
            "orientation = COALESCE(orientation, ?), "
            "aspect_ratio = COALESCE(aspect_ratio, ?), "
            "latitude = COALESCE(?, latitude), "
            "longitude = COALESCE(?, longitude) "
            "WHERE id = ?",
            update_tuple,
        )
        conn.commit()
    finally:
        conn.close()


def _hash_from_open(
    filepath: str,
    *,
    source_data: bytes | None,
    source_bytes: int | None,
) -> tuple[str, bytes | None]:
    """Compute content_hash without a second full-file pass when bytes are warm."""
    if source_data is not None:
        size = int(source_bytes if source_bytes is not None else len(source_data))
        return compute_content_hash_from_prefix(source_data, size), None
    st = os.stat(filepath)
    with open(filepath, "rb") as handle:
        prefix = handle.read(HASH_PREFIX_BYTES)
    return compute_content_hash_from_prefix(prefix, int(st.st_size)), prefix


def _read_original_bytes(filepath: str) -> bytes:
    """Single sequential spindle read of the whole original into RAM."""
    with open(filepath, "rb") as handle:
        return handle.read()


def harvest_side_products(
    filepath: str,
    image_id: int,
    *,
    need_hash: bool,
    need_metadata: bool,
    source_data: bytes | None = None,
    source_bytes: int | None = None,
    pil_image: Any | None = None,
    persist_hash: Callable[[int, str], bool] | None = None,
    persist_metadata: Callable[[int, dict], bool] | None = None,
    source_root: str = "",
) -> HarvestResult:
    """Fill missing hash/metadata from a decode that already touched the file."""
    result = HarvestResult()
    if not need_hash and not need_metadata:
        return result

    if need_hash:
        digest, _prefix = _hash_from_open(
            filepath,
            source_data=source_data,
            source_bytes=source_bytes,
        )
        result.content_hash = digest
        if persist_hash is not None and persist_hash(image_id, digest):
            result.hash_written = True
            result.products.append("content_hash")

    if need_metadata and persist_metadata is not None:
        import photo_metadata

        if source_data is not None:
            metadata = photo_metadata.extract_image_metadata_from_bytes(filepath, source_data)
        elif pil_image is not None:
            metadata = photo_metadata.extract_image_metadata_from_image(filepath, pil_image)
        else:
            # Side-only harvest (no prior decode): one purposeful open.
            metadata = photo_metadata.extract_image_metadata(filepath)
        if source_root:
            metadata["source_root"] = source_root
        if persist_metadata(image_id, metadata):
            result.metadata_written = True
            result.products.append("metadata")
    return result


def harvest_original(
    filepath: str,
    image_id: int,
    *,
    bulk: bool = False,
    size_signatures: dict[str, str] | None = None,
    full_item: dict | None = None,
    source_bytes: int | None = None,
    hot: bool = False,
    need_hash: bool = False,
    need_metadata: bool = False,
    requested_size: str | None = None,
    include_smaller_tiers: bool = False,
    allow_stale_fallback: bool = True,
    generate_thumbnail_set: Callable[..., dict] | None = None,
    generate_missing_thumbnails: Callable[..., object] | None = None,
    persist_hash: Callable[[int, str], bool] | None = None,
    persist_metadata: Callable[[int, dict], bool] | None = None,
    source_root: str = "",
) -> HarvestResult:
    """Single entry: produce all missing derived products from one original touch.

    Bulk callers set ``bulk=True`` so the HDD governor serializes spindle IO.
    The slot is held only for the file read into RAM; demosaic/decode/encode
    run after release. Interactive on-demand callers leave ``bulk=False``.

    Catalog persists for hash/metadata happen *after* the governor slot is
    released so sync SQLite writes cannot deadlock against the async catalog
    connection that is waiting on the harvest thread.
    """
    pending_side: dict[str, Any] = {
        "source_data": None,
        "pil_image": None,
        "file_size": source_bytes,
        "precomputed_hash": None,
    }

    def on_source_loaded(source_data: bytes | None, pil_image) -> None:
        pending_side["source_data"] = source_data
        pending_side["pil_image"] = pil_image
        if source_data is not None and pending_side["file_size"] is None:
            pending_side["file_size"] = len(source_data)

    gate = hdd_governor.bulk_hdd_slot_sync if bulk else nullcontext
    will_generate = (
        size_signatures is not None
        or requested_size is not None
        or full_item is not None
    )

    # --- Bulk: read under the slot, decode outside ---------------------------------
    preloaded: bytes | None = None
    pre_read_seconds = 0.0
    if bulk and will_generate:
        with gate():
            try:
                started = time.monotonic()
                preloaded = _read_original_bytes(filepath)
                pre_read_seconds = max(0.0, time.monotonic() - started)
            except OSError:
                # Fall through to generate so mark_source_missing still runs.
                preloaded = None
                pre_read_seconds = 0.0
        if preloaded is not None:
            pending_side["source_data"] = preloaded
            pending_side["file_size"] = len(preloaded)
            if need_hash:
                try:
                    digest, _prefix = _hash_from_open(
                        filepath,
                        source_data=preloaded,
                        source_bytes=len(preloaded),
                    )
                    pending_side["precomputed_hash"] = digest
                except OSError:
                    pass

    # Decode / encode / write — outside the bulk HDD slot (or never gated).
    result = _harvest_body(
        filepath,
        image_id,
        size_signatures=size_signatures,
        full_item=full_item,
        source_bytes=source_bytes if preloaded is None else len(preloaded),
        hot=hot,
        need_hash=False,
        need_metadata=False,
        requested_size=requested_size,
        include_smaller_tiers=include_smaller_tiers,
        allow_stale_fallback=allow_stale_fallback,
        generate_thumbnail_set=generate_thumbnail_set,
        generate_missing_thumbnails=generate_missing_thumbnails,
        persist_hash=None,
        persist_metadata=None,
        source_root=source_root,
        on_source_loaded=on_source_loaded if (need_hash or need_metadata) else None,
        source_data=preloaded,
    )
    if preloaded is not None:
        # Spindle time was measured under the gate; decode metrics stay separate.
        result.read_seconds = pre_read_seconds
        result.source_bytes = max(result.source_bytes, len(preloaded))
        if result.source_reads <= 0 and not result.source_read_failures:
            result.source_reads = 1

    if result.source_read_failures or not (need_hash or need_metadata):
        return result

    # Side-only harvest (no thumb work) still needs to touch the original under
    # the gate — read into RAM, then hash/metadata outside.
    side_only = (
        size_signatures is None
        and requested_size is None
        and full_item is None
        and result.source_reads <= 0
    )
    if side_only:
        side_bytes: bytes | None = None
        if bulk:
            with gate():
                try:
                    side_bytes = _read_original_bytes(filepath)
                except OSError:
                    return HarvestResult(source_read_failures=1)
            side = harvest_side_products(
                filepath,
                image_id,
                need_hash=need_hash,
                need_metadata=need_metadata,
                source_data=side_bytes,
                source_bytes=len(side_bytes),
                persist_hash=persist_hash,
                persist_metadata=persist_metadata,
                source_root=source_root,
            )
        else:
            # Interactive: bypass the bulk gate (may open the file directly).
            side = harvest_side_products(
                filepath,
                image_id,
                need_hash=need_hash,
                need_metadata=need_metadata,
                persist_hash=persist_hash,
                persist_metadata=persist_metadata,
                source_root=source_root,
            )
        result.source_reads = 1 if (need_hash or need_metadata) else 0
        if side_bytes is not None:
            result.source_bytes = len(side_bytes)
        result.content_hash = side.content_hash
        result.hash_written = side.hash_written
        result.metadata_written = side.metadata_written
        result.products.extend(side.products)
        return result

    if (
        result.source_reads <= 0
        and pending_side["source_data"] is None
        and pending_side["pil_image"] is None
    ):
        return result

    try:
        file_size = pending_side["file_size"]
        if file_size is None:
            try:
                file_size = int(os.stat(filepath).st_size)
            except OSError:
                file_size = None
        if pending_side["precomputed_hash"] is not None and need_hash:
            result.content_hash = pending_side["precomputed_hash"]
            if persist_hash is not None and persist_hash(image_id, pending_side["precomputed_hash"]):
                result.hash_written = True
                result.products.append("content_hash")
            need_hash = False
        side = harvest_side_products(
            filepath,
            image_id,
            need_hash=need_hash,
            need_metadata=need_metadata,
            source_data=pending_side["source_data"],
            source_bytes=file_size,
            pil_image=pending_side["pil_image"],
            persist_hash=persist_hash,
            persist_metadata=persist_metadata,
            source_root=source_root,
        )
    except Exception:
        return result

    result.content_hash = side.content_hash or result.content_hash
    result.hash_written = result.hash_written or side.hash_written
    result.metadata_written = result.metadata_written or side.metadata_written
    for product in side.products:
        if product not in result.products:
            result.products.append(product)
    return result


def _harvest_body(
    filepath: str,
    image_id: int,
    *,
    size_signatures,
    full_item,
    source_bytes,
    hot,
    need_hash,
    need_metadata,
    requested_size,
    include_smaller_tiers,
    allow_stale_fallback,
    generate_thumbnail_set,
    generate_missing_thumbnails,
    persist_hash,
    persist_metadata,
    source_root,
    on_source_loaded,
    source_data: bytes | None = None,
) -> HarvestResult:
    del need_hash, need_metadata, persist_hash, persist_metadata, source_root
    result = HarvestResult()

    if size_signatures is not None or full_item is not None:
        if generate_thumbnail_set is None:
            raise RuntimeError("harvest_original bulk path requires generate_thumbnail_set")
        metrics = generate_thumbnail_set(
            filepath,
            image_id,
            size_signatures or {},
            source_bytes=source_bytes,
            full_item=full_item,
            hot=hot,
            on_source_loaded=on_source_loaded,
            source_data=source_data,
        )
        result.source_reads = int(metrics.get("source_reads") or 0)
        result.thumbnails_written = int(metrics.get("thumbnails_written") or 0)
        result.originals_written = int(metrics.get("originals_written") or 0)
        result.source_bytes = int(metrics.get("source_bytes") or 0)
        result.read_seconds = float(metrics.get("read_seconds") or 0.0)
        result.decode_encode_seconds = float(metrics.get("decode_encode_seconds") or 0.0)
        result.source_read_failures = int(metrics.get("source_read_failures") or 0)
        if result.thumbnails_written:
            result.products.append("thumbs")
        if result.originals_written:
            result.products.append("full")
        return result

    if requested_size is not None:
        if generate_missing_thumbnails is None:
            raise RuntimeError("harvest_original on-demand path requires generate_missing_thumbnails")
        thumb = generate_missing_thumbnails(
            filepath,
            requested_size,
            image_id,
            include_smaller_tiers=include_smaller_tiers,
            hot=hot,
            allow_stale_fallback=allow_stale_fallback,
            on_source_loaded=on_source_loaded,
            source_data=source_data,
        )
        if thumb:
            result.requested_thumb = thumb if isinstance(thumb, (bytes, bytearray)) else None
            result.source_reads = 1
            result.products.append("thumbs")
        elif on_source_loaded is not None:
            # Decode happened even if the requested tier was already warm-written
            # by a concurrent writer; treat as a touch when the hook fired.
            result.source_reads = 1
        elif source_data is not None:
            result.source_reads = 1
        return result

    return result
