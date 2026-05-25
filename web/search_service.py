"""Compatibility facade for query constraint helpers."""

from core.query_constraints import (
    encode_text_with_config,
    normalize_search_query,
)

__all__ = [
    "encode_text_with_config",
    "normalize_search_query",
]
