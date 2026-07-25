"""Shared classification for AI failures that are not caused by source media."""

from __future__ import annotations


def is_gpu_resource_error(error: object) -> bool:
    """Return whether inference failed because the shared GPU lacked capacity."""

    name = type(error).__name__.casefold()
    text = str(error or "").casefold()
    return (
        "outofmemoryerror" in name
        or "cuda out of memory" in text
        or ("cuda" in text and "out of memory" in text)
    )


def caption_ledger_status(*, requested_status: str, error: object) -> str:
    """Keep host resource failures retryable instead of poisoning an image."""

    if requested_status == "error" and is_gpu_resource_error(error):
        return "pending"
    return requested_status
