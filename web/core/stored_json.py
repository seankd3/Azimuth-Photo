"""Reading a JSON object back out of a text column.

Saved queries — smart collections, published nodes — live in the catalog as
text. Anything that is not a JSON object is not a query, and the caller wants
that answer as ``None`` rather than as an exception it must remember to catch.
"""

from __future__ import annotations

import json


def stored_object(value: str | None) -> dict | None:
    """Parse a stored JSON object; return None for absent, invalid or non-object text."""

    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
