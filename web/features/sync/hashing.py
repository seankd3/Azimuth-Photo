"""Stable content identity for hub/satellite synchronization."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


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
    """Return BLAKE2b-128 over every byte for upload integrity verification."""

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
