"""One-process desktop boundary over the V2 product."""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
import sys
import threading
from typing import Awaitable, TypeVar

import webview

import boot
from core.runtime_paths import resolve_runtime_paths


Result = TypeVar("Result")


def default_paths() -> tuple[str, str]:
    runtime = resolve_runtime_paths()
    return (
        os.path.join(runtime.data_dir, "catalog", "azimuth-v2.db"),
        os.path.join(runtime.cache_dir, "v2-tiles"),
    )


def bundled_document() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "desktop" / "index.html"
    return Path(__file__).parents[1] / "build" / "desktop" / "index.html"


class Desktop:
    """The complete JavaScript-facing product vocabulary."""

    def __init__(self, catalog_path: str, tile_root: str):
        self._product = boot.OwnedLibrary(catalog_path, tile_root)
        self._window = None
        self._close_lock = threading.Lock()
        self._closed = False
        self._wait(self._product.run(lambda library: library.start()))

    @staticmethod
    def _wait(answer: Awaitable[Result]) -> Result:
        return asyncio.run(answer)

    def bind(self, window) -> None:
        self._window = window

    def counts(self) -> dict:
        return self._wait(self._product.run(lambda library: library.counts()))

    def drives(self) -> list[dict]:
        return self._wait(self._product.run(lambda library: library.attached()))

    def photos(self, sort: str = "newest", limit: int = 200, offset: int = 0) -> list[dict]:
        return self._wait(
            self._product.run(
                lambda library: library.browse(sort=sort, limit=int(limit), offset=int(offset))
            )
        )

    def photo(self, photo_id: int) -> dict:
        answer = self._wait(
            self._product.run(lambda library: library.details(int(photo_id)))
        )
        if answer is None:
            raise ValueError("photo is unavailable")
        return answer

    def pick(self, photo_ids: list[int]) -> dict:
        return self._wait(
            self._product.run(lambda library: library.pick(photo_ids))
        )

    def clear_pick(self, photo_ids: list[int]) -> dict:
        return self._wait(
            self._product.run(lambda library: library.clear_pick(photo_ids))
        )

    def reject(self, photo_ids: list[int]) -> dict:
        return self._wait(
            self._product.run(lambda library: library.reject(photo_ids))
        )

    def restore(self, photo_ids: list[int]) -> dict:
        return self._wait(
            self._product.run(lambda library: library.restore(photo_ids))
        )

    def undo_cull(self, changes: list[dict]) -> dict:
        return self._wait(
            self._product.run(lambda library: library.undo_cull(changes))
        )

    def trash_count(self) -> int:
        return self._wait(self._product.run(lambda library: library.trash_count()))

    def trash_photos(self, limit: int = 200, offset: int = 0) -> list[dict]:
        return self._wait(
            self._product.run(
                lambda library: library.browse_trash(
                    limit=int(limit), offset=int(offset)
                )
            )
        )

    def empty_trash(self, expected_count: int, dry_run: bool = False) -> dict:
        return self._wait(
            self._product.run(
                lambda library: library.empty_trash(
                    int(expected_count), dry_run=bool(dry_run)
                )
            )
        )

    def tile(self, photo_id: int, size: int = 400) -> str:
        body = self._wait(
            self._product.run(
                lambda library: library.tile(int(photo_id), size=int(size))
            )
        )
        if body is None:
            raise ValueError("photo is unavailable")
        return "data:image/jpeg;base64," + base64.b64encode(body).decode("ascii")

    def attach(self, root: str, is_record: bool = False) -> dict:
        return self._wait(
            self._product.run(
                lambda library: library.attach(root, is_record=bool(is_record))
            )
        )

    def refresh(self, drive_uuid: str) -> dict:
        return self._wait(self._product.refresh(str(drive_uuid)))

    def choose_folder(self) -> str:
        if self._window is None:
            raise RuntimeError("desktop window is unavailable")
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return chosen[0] if chosen else ""

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._wait(self._product.close())
            self._closed = True


def main() -> int:
    catalog_path, tile_root = default_paths()
    desktop = Desktop(catalog_path, tile_root)
    document = bundled_document().read_text(encoding="utf-8")
    window = webview.create_window(
        "Azimuth Photo",
        html=document,
        js_api=desktop,
        width=1500,
        height=950,
        min_size=(900, 600),
        background_color="#0d0e10",
    )
    desktop.bind(window)
    window.events.closed += desktop.close
    try:
        webview.start(gui="edgechromium")
    finally:
        desktop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
