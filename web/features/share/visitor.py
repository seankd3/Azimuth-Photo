"""What someone outside this library sees.

Three surfaces show photos to a stranger — a published site, a client gallery,
and a share link — and each had grown its own copy of the same three answers:
who the photographer is, what to call their site, and which headers keep a
public page from being cached or from leaking a referrer.

They are one set of answers, so they live in one place. `share` is the common
base of the other two, which already depend on it.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import Response

import settings

_SAFE_STEM_CHARACTERS = "._- "
_MAX_STEM = 180
_MAX_EXTENSION = 12


def site_label(site_url: str) -> str:
    """The bare hostname a visitor recognises: ``www.example.com`` → ``example.com``."""

    if not site_url:
        return ""
    parsed = urlparse(site_url if "://" in site_url else f"https://{site_url}")
    return (parsed.netloc or parsed.path).removeprefix("www.")


def brand() -> dict:
    """The photographer's name and site, as shown on any public page."""

    config = settings.get_settings()
    url = str(config.get("publish_site_base_url") or "").strip().rstrip("/")
    label = site_label(url)
    name = str(config.get("share_brand_name") or "").strip() or label or "Your photographer"
    return {"name": name, "site_url": url, "site_label": label}


def no_leak(response: Response) -> Response:
    """Mark a response as belonging to one visitor: never cached, never referred."""

    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-store"
    return response


def attachment_name(image_id: int, filename: str, *, suffix: str = "") -> str:
    """A download filename that is safe on every filesystem and never empty."""

    name = Path(filename or "").name
    stem, _, extension = name.rpartition(".") if "." in name else (name, "", "")
    safe_stem = "".join(c if c.isalnum() or c in _SAFE_STEM_CHARACTERS else "_" for c in stem)
    safe_stem = safe_stem.strip(" .")[:_MAX_STEM]
    if not any(c.isalnum() for c in safe_stem):
        # Nothing recognisable survived escaping — "___.jpg" helps nobody.
        safe_stem = f"photo-{image_id}"
    wanted = suffix.lstrip(".") or extension
    safe_extension = "".join(c for c in wanted.lower() if c.isalnum())[:_MAX_EXTENSION]
    return f"{safe_stem}.{safe_extension}" if safe_extension else safe_stem
