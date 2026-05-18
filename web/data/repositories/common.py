"""Shared helpers for repository query batching."""


def chunked(values, chunk_size: int = 500):
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]
