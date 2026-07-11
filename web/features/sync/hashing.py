"""Stable content identity for hub/satellite synchronization."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


HASH_PREFIX_BYTES = 8 * 1024 * 1024
HASH_DIGEST_BYTES = 16


def compute_content_hash(path: str | os.PathLike[str]) -> str:
    """Return the exact FIELD_SPEC identity digest.

    The hashed bytes are ``file[0:min(size, 8 MiB)]`` followed immediately by
    the file size encoded as one unsigned 8-byte little-endian integer. BLAKE2b
    uses a 16-byte digest and the result is lowercase hexadecimal.
    """

    candidate = Path(path)
    file_size = candidate.stat().st_size
    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with candidate.open("rb") as handle:
        digest.update(handle.read(HASH_PREFIX_BYTES))
    digest.update(int(file_size).to_bytes(8, byteorder="little", signed=False))
    return digest.hexdigest()


def compute_full_hash(path: str | os.PathLike[str]) -> str:
    """Return BLAKE2b-128 over every byte for upload integrity verification."""

    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
