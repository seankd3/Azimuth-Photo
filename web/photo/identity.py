"""Every photo knows what it is.

Identity is a hash of the bytes, not the path, and every derived artifact —
thumbnail, Develop base, embedding, caption, face vector — is keyed on it. A
photo without one cannot take part in any of them.

It used to be filled in by accident: nothing in the scan path wrote a hash,
`thumbnails/harvest.py` set one as a side effect of making a thumbnail, and the
only deliberate filler was a route with no caller.

Cursor-free on purpose. Hashing a photo removes it from its own candidate set,
so the query advances itself; a bookmark would be wrong here the same way it was
for embeddings, where `id > mark` under a newest-first order skips everything
imported before the mark.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from core import hdd_governor
from data import connection
from photo.visibility import visible_image_condition

log = logging.getLogger(__name__)

BATCH = 100

HASH_PREFIX_BYTES = 8 * 1024 * 1024
HASH_DIGEST_BYTES = 16


def compute_content_hash_from_prefix(prefix: bytes, file_size: int) -> str:
    """FIELD_SPEC digest from an already-read prefix (read-once harvest path)."""
    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    digest.update(prefix[:HASH_PREFIX_BYTES])
    digest.update(int(file_size).to_bytes(8, byteorder="little", signed=False))
    return digest.hexdigest()


def compute_content_hash(path: str | os.PathLike[str]) -> str:
    """Return the exact FIELD_SPEC identity digest.

    The hashed bytes are ``file[0:min(size, 8 MiB)]`` followed immediately by
    the file size encoded as one unsigned 8-byte little-endian integer. BLAKE2b
    uses a 16-byte digest and the result is lowercase hexadecimal.
    """

    candidate = Path(path)
    file_size = candidate.stat().st_size
    with candidate.open("rb") as handle:
        prefix = handle.read(HASH_PREFIX_BYTES)
    return compute_content_hash_from_prefix(prefix, file_size)


def compute_full_hash(path: str | os.PathLike[str]) -> str:
    """Return BLAKE2b-128 over every byte for copy integrity verification."""

    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def compute_hash_pair(path: str | os.PathLike[str]) -> tuple[str, str]:
    """Return the fast identity and complete proof in one sequential read."""

    candidate = Path(path)
    before = candidate.stat()
    content_digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    full_digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    prefix_remaining = HASH_PREFIX_BYTES
    with candidate.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            full_digest.update(chunk)
            if prefix_remaining:
                prefix = chunk[:prefix_remaining]
                content_digest.update(prefix)
                prefix_remaining -= len(prefix)
    after = candidate.stat()
    if (
        int(before.st_size) != int(after.st_size)
        or int(before.st_mtime_ns) != int(after.st_mtime_ns)
    ):
        raise OSError(f"File changed while hashing: {candidate}")
    content_digest.update(
        int(before.st_size).to_bytes(8, byteorder="little", signed=False)
    )
    return content_digest.hexdigest(), full_digest.hexdigest()
BETWEEN_BATCHES_SECONDS = 0.05
WHEN_CAUGHT_UP_SECONDS = 300.0

# Newest first: the photos worth deriving anything for are the ones that just
# came off the card. INDEXED BY rather than left to the planner — measured on
# 150k rows, SQLite prefers `idx_images_missing_date_source` plus a temp B-tree
# for the sort (25.1 ms against 1.7 ms) until someone runs ANALYZE, and nothing
# in this app ever does.
_NEEDS_IDENTITY = f"""
    SELECT id, filepath FROM images INDEXED BY idx_images_needs_identity
    WHERE content_hash IS NULL AND {visible_image_condition('')}
    ORDER BY date_taken DESC, id DESC
    LIMIT ? OFFSET ?
"""

_status: dict[str, Any] = {"state": "idle", "hashed": 0, "unreadable": 0}


def status() -> dict[str, Any]:
    return dict(_status)


def _hash_under_governor(path: str) -> str:
    with hdd_governor.bulk_hdd_slot_sync():
        return compute_content_hash(path)


async def fill_one_batch(db_path: str, *, skip: int = 0) -> tuple[int, int]:
    """Give up to `BATCH` photos an identity. Returns (hashed, considered).

    The connection is closed while hashing: a batch is up to 800 MiB of reads
    off a cold archive drive, which is not a thing to hold a catalog open across.
    """

    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(_NEEDS_IDENTITY, (BATCH, skip))).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if not rows:
        return 0, 0

    found: list[tuple[str, int]] = []
    for row in rows:
        try:
            found.append((await asyncio.to_thread(_hash_under_governor, row["filepath"]), int(row["id"])))
        except OSError:
            _status["unreadable"] += 1

    if found:
        conn = await connection.open_async(db_path)
        try:
            await conn.executemany(
                "UPDATE images SET content_hash = ? WHERE id = ? AND content_hash IS NULL", found
            )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)
        _status["hashed"] += len(found)
    return len(found), len(rows)


async def run_identity_backfill(db_path: Any) -> None:
    """Give every photo an identity, for as long as the app runs.

    `skip` steps over a window whose files cannot be read right now — an offline
    source at the head of the newest-first queue would otherwise stall the whole
    backlog behind it. Any successful batch clears it, so it never accumulates.
    """

    skip = 0
    while True:
        try:
            hashed, considered = await fill_one_batch(db_path() if callable(db_path) else db_path, skip=skip)
        except Exception:
            log.exception("worker=identity batch failed")
            _status["state"] = "error"
            await asyncio.sleep(WHEN_CAUGHT_UP_SECONDS)
            continue

        if hashed:
            skip = 0
            _status["state"] = "running"
        elif considered:
            skip += considered
            _status["state"] = "waiting_on_source"
        else:
            skip = 0
            _status["state"] = "caught_up"
            await asyncio.sleep(WHEN_CAUGHT_UP_SECONDS)
            continue
        await asyncio.sleep(BETWEEN_BATCHES_SECONDS)
