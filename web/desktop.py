"""One-process desktop boundary over the V2 product."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading
from typing import Awaitable, Callable, TypeVar

import webview

import boot
import home
from model.scope import EVERYTHING, folder as in_folder


Result = TypeVar("Result")


def bundled_document() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "desktop" / "index.html"
    return Path(__file__).parents[1] / "build" / "desktop" / "index.html"


class Desktop:
    """The complete JavaScript-facing product vocabulary.

    A desktop may open without a home -- the first run -- and then every
    library verb refuses until one is chosen; choosing is the one verb that
    is always available, and it is remembered so the next start needs no
    question.
    """

    def __init__(self, home_path: str | None, *, follow: bool = True):
        self._follow = follow
        self._product: boot.OwnedLibrary | None = None
        self._home: str | None = None
        self._window = None
        self._close_lock = threading.Lock()
        self._closed = False
        if home_path:
            self._settle(home_path)

    @staticmethod
    def _wait(answer: Awaitable[Result]) -> Result:
        return asyncio.run(answer)

    def _run(self, operation: Callable[[boot.Library], Result]) -> Result:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.run(operation))

    def _settle(self, path: str) -> None:
        catalog_path, previews = home.paths(path)
        self._product = boot.OwnedLibrary(catalog_path, previews)
        self._home = path
        self._wait(self._product.run(lambda library: library.start()))
        if self._follow:
            self._product.follow()

    def bind(self, window) -> None:
        self._window = window

    # ---- the home ----

    def home(self) -> str | None:
        return self._home

    def propose_home(self) -> str:
        return home.propose()

    def settle_home(self, path: str) -> str:
        if self._product is not None:
            raise RuntimeError("this library is already open")
        settled = home.remember(path)
        self._settle(settled)
        return settled

    # ---- the library ----

    def counts(self) -> dict:
        return self._run(lambda library: library.counts())

    def drives(self) -> list[dict]:
        return self._run(lambda library: library.attached())

    def pulse(self) -> dict:
        pulse = self._run(lambda library: library.pulse())
        pulse["cards"] = len(self._product.cards)
        return pulse

    def photos(self, sort: str = "newest", limit: int = 200, offset: int = 0,
               folder: str | None = None) -> list[dict]:
        scope = in_folder(folder) if folder else EVERYTHING
        return self._run(
            lambda library: library.browse(scope=scope, sort=sort, limit=int(limit), offset=int(offset))
        )

    def size(self, folder: str | None = None) -> int:
        scope = in_folder(folder) if folder else EVERYTHING
        return self._run(lambda library: library.size(scope))

    def folders(self) -> list[dict]:
        return self._run(lambda library: library.folders())

    def photo(self, photo_id: int) -> dict:
        # No embedded facts yet -- the photograph is not identified, or its
        # drive is away -- is a normal answer, not a failure: the row the
        # window already holds stands until the facts arrive.
        return self._run(lambda library: library.details(int(photo_id))) or {}

    def pick(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.pick(photo_ids))

    def clear_pick(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.clear_pick(photo_ids))

    def reject(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.reject(photo_ids))

    def restore(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.restore(photo_ids))

    def undo_cull(self, changes: list[dict]) -> dict:
        return self._run(lambda library: library.undo_cull(changes))

    def turn(self, photo_ids: list[int], by: int = 90) -> dict:
        return self._run(lambda library: library.turn(photo_ids, by=int(by)))

    def forget(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.forget(photo_ids))

    def forget_missing(self, folder: str = "") -> dict:
        return self._run(lambda library: library.forget_missing(str(folder or "")))

    # ---- bringing photographs in ----

    def cards(self) -> list[dict]:
        return list(self._product.cards) if self._product else []

    def stage(self, source: str) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.stage(str(source)))

    def bring(self, source: str, keys: list[str], kind: str, clear_source: bool = False,
              roll: str = "") -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.bring(str(source), list(keys), str(kind),
                                              clear_source=bool(clear_source), roll=str(roll or "")))

    def intake_status(self) -> dict:
        return self._product.intake_status() if self._product else {"phase": "idle"}

    def stop_intake(self) -> None:
        if self._product:
            self._product.stop_intake()

    def thumb(self, source: str, key: str) -> str | None:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._product.thumb(str(source), str(key))

    def synchronize(self, folder: str = "") -> list[dict]:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.synchronize(str(folder or "")))

    def trash_count(self) -> int:
        return self._run(lambda library: library.trash_count())

    def trash_photos(self, limit: int = 200, offset: int = 0) -> list[dict]:
        return self._run(
            lambda library: library.browse_trash(limit=int(limit), offset=int(offset))
        )

    def empty_trash(self, expected_count: int, dry_run: bool = False) -> dict:
        return self._run(
            lambda library: library.empty_trash(int(expected_count), dry_run=bool(dry_run))
        )

    def look(self, photo_ids: list[int]) -> int:
        return self._run(lambda library: library.look(photo_ids))

    def attach(self, root: str, is_record: bool = False) -> dict:
        return self._run(lambda library: library.attach(root, is_record=bool(is_record)))

    def refresh(self, drive_uuid: str) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
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
            if self._product is not None:
                self._wait(self._product.close())
            self._closed = True


def main() -> int:
    desktop = Desktop(home.current())
    # The document is opened from its file rather than handed over as a
    # string: a page with a file origin may show a tile straight from the
    # store (`<img src="file:///...">`, measured 5 ms), while an inlined page
    # has no origin and every picture would have to cross the bridge encoded.
    window = webview.create_window(
        "Azimuth Photo",
        url=bundled_document().as_uri(),
        js_api=desktop,
        width=1500,
        height=950,
        min_size=(900, 600),
        # Opened maximized: the owner works in the full window, and every
        # surface is designed at that size first.
        maximized=True,
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
