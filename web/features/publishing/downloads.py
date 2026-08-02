"""ZIP delivery for frozen public gallery image sets."""

from __future__ import annotations

from features.share.visitor import (
    attachment_name as _attachment_name,
)

import asyncio
import json
import zipfile
from http.client import IncompleteRead
from pathlib import Path

from core.source_files import source_file_is_safe
from features.sync import readthrough


def zip_gallery(gallery: dict, destination: str) -> dict:
    """Build a ZIP from already-authorized frozen images at the selected tier."""
    import thumbnails

    size = gallery["download_size"]
    skipped, included = [], 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in gallery["images"]:
            image_id, filename = int(image["id"]), Path(image["filename"])
            remote = int(image.get("hub_remote") or 0) == 1
            try:
                if remote:
                    response = readthrough.open_hub_original(int(image.get("hub_image_id") or 0)) if size == "original" else readthrough.open_hub_preview(int(image.get("hub_image_id") or 0), size)
                    if response is None:
                        raise FileNotFoundError("Hub media unavailable")
                    try:
                        archive.writestr(_attachment_name(image_id, filename.name, suffix="" if size == "original" else ".jpg"), response.read())
                    finally:
                        response.close()
                elif size == "original":
                    if not source_file_is_safe(str(image.get("filepath") or ""), str(image.get("source_path") or "")):
                        raise FileNotFoundError("Local original unavailable")
                    archive.write(image["filepath"], arcname=_attachment_name(image_id, filename.name))
                else:
                    data = asyncio.run(thumbnails.get_thumbnail(image["filepath"], size, image_id))
                    if not data:
                        raise FileNotFoundError("Preview unavailable")
                    archive.writestr(_attachment_name(image_id, filename.name, suffix=".jpg"), data)
                included += 1
            except (IncompleteRead, OSError, ValueError):
                skipped.append({"image_id": image_id, "filename": filename.name, "reason": "hub unavailable" if remote else "source unavailable"})
        if skipped:
            archive.writestr("azimuth-download-manifest.json", json.dumps({"requested_count": len(gallery["images"]), "included_count": included, "skipped_count": len(skipped), "skipped": skipped}, indent=2))
    return {"path": destination, "included": included, "skipped": skipped}
