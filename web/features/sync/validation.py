"""Shared validation for the hub and oplog sync surfaces."""

from __future__ import annotations

import re


_CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_content_hash(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _CONTENT_HASH_RE.fullmatch(normalized):
        raise ValueError("content_hash must be a 32-character BLAKE2b-128 hex digest")
    return normalized
