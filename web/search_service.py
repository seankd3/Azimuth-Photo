"""Compatibility facade for query constraint helpers."""

from core.query_constraints import (
    encode_text_with_config,
    normalize_search_query,
    resolve_cached_deep_search,
)

__all__ = [
    "encode_text_with_config",
    "normalize_search_query",
    "resolve_cached_deep_search",
]
