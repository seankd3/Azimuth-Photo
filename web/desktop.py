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


Result = TypeVar("Result")


def bundled_document() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "desktop" / "index.html"
    return Path(__file__).parents[1] / "build" / "desktop" / "index.html"


def bundled_icon() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "desktop" / "icon.ico"
    return Path(__file__).parents[1] / "desktop" / "icon.ico"


def wear_the_mark(window) -> None:
    """The compass on the title bar and the taskbar. Python windows otherwise
    wear the interpreter's icon, and the taskbar groups them under python.

    Win32 messages only: the shown event fires off the UI thread, and
    WinForms is thread-affine — assigning `native.Icon` from here corrupted
    window activation (the app stopped coming to the foreground). SendMessage
    marshals to the window's own thread by design, so WM_SETICON is the safe
    spelling of the same wish.
    """

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AzimuthPhoto.Desktop")
    except Exception:
        pass
    icon = bundled_icon()
    if not icon.is_file():
        return

    def dress():
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "Azimuth Photo")
            if not hwnd:
                return
            IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x0010, 0x0080
            for which, size in ((0, 16), (1, 32)):   # ICON_SMALL, ICON_BIG
                handle = user32.LoadImageW(None, str(icon), IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if handle:
                    user32.SendMessageW(hwnd, WM_SETICON, which, handle)
        except Exception:
            pass

    window.events.shown += dress


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
        # The rank lane's derived rewrites (clusters, people): the window
        # re-asks for them when this moves, same as done and swept.
        pulse["shaped"] = self._product.shaped
        return pulse

    def photos(self, sort: str = "newest", limit: int = 200, offset: int = 0,
               view: dict | None = None) -> list[dict]:
        return self._run(
            lambda library: library.browse(
                scope=library.viewing(view), sort=sort, limit=int(limit), offset=int(offset))
        )

    def size(self, view: dict | None = None) -> int:
        return self._run(lambda library: library.size(library.viewing(view)))

    def identifiers(self, view: dict | None = None, trashed: bool = False) -> list[int]:
        return self._run(lambda library: library.identifiers(
            library.viewing(view), trashed=bool(trashed)))

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

    def develop(self, photo_id: int, patch: dict) -> dict:
        return self._run(lambda library: library.develop(int(photo_id), patch or {}))

    def develop_preview(self, photo_id: int, patch: dict, size: int = 1280) -> str:
        return self._run(lambda library: library.develop_preview(int(photo_id), patch or {}, int(size)))

    def develop_state(self, photo_id: int) -> dict:
        return self._run(lambda library: library.develop_state(int(photo_id)))

    def export_settings(self, photo_ids: list[int] | None = None, folder: str = "") -> dict:
        return self._run(lambda library: library.export_settings(
            None if photo_ids is None else [int(i) for i in photo_ids], folder=str(folder or "")))

    def days(self, view: dict | None = None) -> list:
        return self._run(lambda library: library.days(view))

    def sessions(self) -> list:
        return self._run(lambda library: library.sessions())

    def search(self, query: str, limit: int = 200, offset: int = 0,
               view: dict | None = None, like: list | None = None) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.find(
            str(query or ""), int(limit), int(offset), view,
            like=[int(i) for i in (like or [])]))

    # ---- albums ----

    def albums(self) -> list[dict]:
        return self._run(lambda library: library.albums())

    def create_album(self, name: str, chips: list | None = None) -> dict:
        return self._run(lambda library: library.create_album(str(name), chips or None))

    def rename_album(self, set_id: str, name: str) -> dict | None:
        return self._run(lambda library: library.rename_album(str(set_id), str(name)))

    def forget_album(self, set_id: str) -> bool:
        return self._run(lambda library: library.forget_album(str(set_id)))

    def add_to_album(self, set_id: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.add_to_album(str(set_id), photo_ids))

    def remove_from_album(self, set_id: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.remove_from_album(str(set_id), photo_ids))

    def quick(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.quick(photo_ids))

    def freeze_album(self, set_id: str) -> dict:
        return self._run(lambda library: library.freeze_album(str(set_id)))

    def save_view(self, name: str, view: dict | None = None) -> dict:
        return self._run(lambda library: library.save_view(str(name), view))

    def save_photos(self, name: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.save_photos(str(name), photo_ids))

    def cameras(self) -> list[dict]:
        return self._run(lambda library: library.cameras())

    def facets(self) -> dict:
        return self._run(lambda library: library.facets())

    def people(self) -> list[dict]:
        return self._run(lambda library: library.people())

    def labels(self) -> list[dict]:
        return self._run(lambda library: library.labels())

    def teach(self, word: str, photo_ids: list[int], yes: bool) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        # The one word recomputes against the memoized space before this
        # returns, so the window's next read already holds the teaching;
        # the rank lane reconciles the whole answer behind it.
        space = self._product.spaced()
        said = self._run(lambda library: library.teach(str(word), photo_ids, bool(yes), space=space))
        self._product.shaped += 1
        self._product.rank_soon()
        return said

    def name_person(self, exemplar: str, called: str) -> dict:
        said = self._run(lambda library: library.name_person(str(exemplar), str(called)))
        # The groups rewrite around the new name on the rank lane.
        self._product.rank_soon()
        return said

    # ---- rank ----

    def rank(self, n: int = 9, view: dict | None = None, avoid: list[str] | None = None,
             mode: str = "close") -> dict:
        space = self._product.spaced()
        return self._run(lambda library: library.rank(
            int(n), view, list(avoid or []), mode=str(mode or "close"), space=space))

    def round(self, winner_id: int, over_ids: list[int]) -> dict:
        recorded = self._run(lambda library: library.round(int(winner_id), over_ids))
        self._product.rank_soon()
        return recorded

    def unround(self, decision: int) -> dict:
        retracted = self._run(lambda library: library.unround(int(decision)))
        self._product.rank_soon()
        return retracted

    def forget(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.forget(photo_ids))

    def forget_missing(self, folder: str = "", dry: bool = False) -> dict:
        return self._run(lambda library: library.forget_missing(str(folder or ""), dry=bool(dry)))

    # ---- bringing photographs in ----

    def cards(self) -> list[dict]:
        return list(self._product.cards) if self._product else []

    def stage(self, source: str) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.stage(str(source)))

    def bring(self, source: str, keys: list[str], kind: str, clear_source: bool = False,
              roll: str = "", rolls: dict | None = None) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.bring(str(source), list(keys), str(kind),
                                              clear_source=bool(clear_source), roll=str(roll or ""),
                                              rolls={str(k): str(v) for k, v in (rolls or {}).items()}))

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

    def export_photos(self, photo_ids: list[int], quality: int = 92,
                      long_edge: int = 0, rename: str = "") -> dict:
        """Pick a folder and export the chosen photographs into it."""

        if self._window is None:
            raise RuntimeError("desktop window is unavailable")
        chosen = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not chosen:
            return {"chosen": False}
        return {**self._wait(self._product.export_files(
            photo_ids, chosen[0], quality=int(quality), long_edge=int(long_edge),
            rename=str(rename or ""))), "chosen": True}

    def adopt_track(self) -> dict:
        """Pick a GPX file and place every photograph its span covers."""

        if self._window is None:
            raise RuntimeError("desktop window is unavailable")
        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("GPS tracks (*.gpx)",))
        if not chosen:
            return {"placed": 0, "chosen": False}
        return {**self._wait(self._product.adopt_track(chosen[0])), "chosen": True}

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
        background_color="#0a0c0e",
    )
    desktop.bind(window)
    wear_the_mark(window)
    window.events.closed += desktop.close
    try:
        webview.start(gui="edgechromium")
    finally:
        desktop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
