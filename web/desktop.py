"""One-process desktop boundary over the V2 product."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading
import time
from typing import Awaitable, Callable, TypeVar

import webview

import boot
import home


Result = TypeVar("Result")

# Launch, as the clock the startup marks are read against: the log says
# when the window showed and when the first page answered, in seconds
# from here, so every real launch measures itself.
_LAUNCHED = time.perf_counter()


def mark(what: str) -> None:
    import logging

    logging.getLogger("azimuth").info("%s at +%.2fs", what, time.perf_counter() - _LAUNCHED)


def bundled_document() -> Path:
    """The one document the window opens. In a checkout it follows its
    sources: when any file under `web/static/v2/` or the template is newer
    than the built document, it is built again here (half a second), so
    the window always shows the UI the tree says and never the one built
    last week. A build that fails is a launch that refuses, and says why."""

    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "desktop" / "index.html"
    root = Path(__file__).parents[1]
    made = root / "build" / "desktop" / "index.html"
    sources = [root / "web" / "templates" / "v2.html", *(p for p in (root / "web" / "static" / "v2").rglob("*") if p.is_file())]
    newest = max(p.stat().st_mtime for p in sources)
    if not made.is_file() or made.stat().st_mtime < newest:
        import logging
        import subprocess

        built = subprocess.run([sys.executable, str(root / "scripts" / "build_desktop_ui.py")],
                               capture_output=True, text=True, cwd=root, check=False)
        if built.returncode != 0:
            logging.getLogger("azimuth").error("the desktop document did not build: %s", built.stderr.strip())
            raise SystemExit("the desktop document did not build; see logs/azimuth.log")
        mark("document built")
    return made


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
        mark("window shown")
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


_MUTEX = None


def one_at_a_time() -> bool:
    """One Azimuth on the machine: a second launch brings the first to the
    front and leaves. Two on one catalog would each rewrite the other's
    answers, and a double-clicked shortcut is the usual way to get two."""

    if not sys.platform.startswith("win"):
        return True
    import ctypes

    global _MUTEX
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, r"Local\AzimuthPhoto.Desktop")
    if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        # The first may still be opening its window (a double-click is two
        # launches a beat apart): look for a while before giving up.
        for _ in range(20):
            hwnd = _our_window()
            if hwnd:
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                break
            time.sleep(0.25)
        return False
    _MUTEX = handle   # held for the life of the process
    return True


def _our_window() -> int:
    """The running app's window: titled Azimuth Photo *and* owned by an
    Azimuth process -- an Explorer window on a folder of that name is not it."""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found = 0

    def visit(hwnd, _lparam):
        nonlocal found
        length = user32.GetWindowTextLengthW(hwnd)
        if length != len("Azimuth Photo"):
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value != "Azimuth Photo":
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return True
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                name = os.path.basename(path.value).lower()
                if name in ("azimuth photo.exe", "pythonw.exe", "python.exe"):
                    found = hwnd
                    return False
        finally:
            kernel32.CloseHandle(process)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found


def keep_a_log(where: str | None) -> None:
    """What went wrong, written down: the last few megabytes of the log
    under the home (`logs/azimuth.log`), and every uncaught error on any
    thread, so a window that closed by itself never has to be guessed at."""

    import logging
    import logging.handlers
    import tempfile

    folder = os.path.join(where or tempfile.gettempdir(), "logs")
    try:
        os.makedirs(folder, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(folder, "azimuth.log"), maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    # The app's own modules speak at INFO; every library keeps its own
    # level, or the file would be theirs.
    ours = {p.stem for p in Path(__file__).parent.glob("*.py")} | {"azimuth", "model", "photo"}
    handler.addFilter(lambda record: record.name.split(".")[0] in ours or record.levelno >= logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    for name in ours:
        logging.getLogger(name).setLevel(logging.INFO)

    def uncaught(kind, value, trace):
        if kind is SystemExit:
            return
        logging.getLogger("azimuth").critical("uncaught", exc_info=(kind, value, trace))

    sys.excepthook = uncaught
    threading.excepthook = lambda args: uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    logging.getLogger("azimuth").info("Azimuth Photo opened from %s", sys.executable)


class Taskbar:
    """The taskbar button's progress bar, the way every long job of a
    professional app shows itself: ITaskbarList3 through ctypes alone, so
    no package is added for it. The window asks from a fresh thread each
    time and a COM object belongs to the thread that made it, so the object
    is made, used and released within the one call. Silent wherever it
    cannot work."""

    def __init__(self):
        self._hwnd = 0
        self._showing = False

    def show(self, done: int, total: int) -> None:
        running = bool(total) and done < total
        if not sys.platform.startswith("win") or not (running or self._showing):
            return
        if not self._hwnd:
            self._hwnd = _our_window()
            if not self._hwnd:
                return
        try:
            import ctypes
            from ctypes import wintypes

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            try:
                class GUID(ctypes.Structure):
                    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

                def guid(text):
                    out = GUID()
                    ole32.CLSIDFromString(text, ctypes.byref(out))
                    return out

                handle = ctypes.c_void_p()
                made = ole32.CoCreateInstance(
                    ctypes.byref(guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")), None, 1,
                    ctypes.byref(guid("{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}")), ctypes.byref(handle))
                if made != 0 or not handle:
                    return
                table = ctypes.cast(ctypes.cast(handle, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                # ITaskbarList3's table: 2 Release, 3 HrInit, 9 SetProgressValue, 10 SetProgressState.
                call = ctypes.WINFUNCTYPE
                try:
                    call(ctypes.HRESULT, ctypes.c_void_p)(table[3])(handle)
                    state = call(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(table[10])
                    if running:
                        state(handle, self._hwnd, 2)   # TBPF_NORMAL
                        call(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong,
                             ctypes.c_ulonglong)(table[9])(handle, self._hwnd, int(done), int(total))
                    else:
                        state(handle, self._hwnd, 0)   # TBPF_NOPROGRESS
                    self._showing = running
                finally:
                    call(ctypes.c_ulong, ctypes.c_void_p)(table[2])(handle)
            finally:
                ole32.CoUninitialize()
        except Exception:  # noqa: BLE001 - a taskbar that will not answer is no error
            pass


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
        self._exported_to: str | None = None
        self._close_lock = threading.Lock()
        self._first_page_said = False
        self._taskbar = Taskbar()
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

    def report(self, text: str) -> None:
        """An error the window caught -- a thrown handler, a rejected
        promise -- written to the log under the home, so a fault on the
        page is read the same way as a fault in the product."""

        import logging

        logging.getLogger("azimuth.window").error("%s", str(text)[:2000])

    def version(self) -> dict:
        """Which build this is: the checkout's commit and when it was made,
        read from the repository's own files (no git process). Empty where
        there is no checkout, and the foot says nothing rather than a build
        it cannot name."""

        root = Path(__file__).resolve().parents[1]
        try:
            head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
            commit = head
            if head.startswith("ref:"):
                ref = head.split(" ", 1)[1].strip()
                try:
                    commit = (root / ".git" / ref).read_text(encoding="utf-8").strip()
                except OSError:
                    # After a gc the ref lives in packed-refs alone.
                    packed = (root / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines()
                    commit = next(line.split()[0] for line in packed if line.endswith(" " + ref))
            last = (root / ".git" / "logs" / "HEAD").read_text(encoding="utf-8").strip().splitlines()[-1]
            stamp = int(last.split(">", 1)[1].split()[0])
            when = time.strftime("%d %b %Y", time.localtime(stamp))
            return {"commit": commit[:8], "when": when}
        except (OSError, IndexError, ValueError):
            return {"commit": "", "when": ""}

    def home(self) -> str | None:
        return self._home

    def propose_home(self) -> str:
        return home.propose()

    def settle_home(self, path: str) -> str:
        if self._product is not None:
            raise RuntimeError("This library is already open.")
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
        page = self._run(
            lambda library: library.browse(
                scope=library.viewing(view), sort=sort, limit=int(limit), offset=int(offset))
        )
        if not self._first_page_said:
            self._first_page_said = True
            mark("first page answered")
        return page

    def size(self, view: dict | None = None) -> int:
        return self._run(lambda library: library.size(library.viewing(view)))

    def position(self, photo_id: int, sort: str = "newest", view: dict | None = None) -> int | None:
        return self._run(lambda library: library.position(int(photo_id), str(sort), view))

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

    def stack(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.stack(photo_ids))

    def unstack(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.unstack(photo_ids))

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

    def remember_album(self, set_id: str) -> bool:
        return self._run(lambda library: library.remember_album(str(set_id)))

    def add_to_album(self, set_id: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.add_to_album(str(set_id), photo_ids))

    def remove_from_album(self, set_id: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.remove_from_album(str(set_id), photo_ids))

    def quick(self, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.quick(photo_ids))

    def freeze_album(self, set_id: str) -> dict:
        return self._run(lambda library: library.freeze_album(str(set_id)))

    def redefine_album(self, set_id: str, criteria) -> dict:
        return self._run(lambda library: library.redefine_album(str(set_id), criteria))

    def save_view(self, name: str, view: dict | None = None) -> dict:
        return self._run(lambda library: library.save_view(str(name), view))

    def save_photos(self, name: str, photo_ids: list[int]) -> dict:
        return self._run(lambda library: library.save_photos(str(name), photo_ids))

    def cameras(self) -> list[dict]:
        return self._run(lambda library: library.cameras())

    def facets(self) -> dict:
        return self._run(lambda library: library.facets())

    def faces(self, photo_id: int) -> list[list[float]]:
        return self._run(lambda library: library.faces(int(photo_id)))

    def people(self) -> list[dict]:
        return self._run(lambda library: library.people())

    def labels(self) -> list[dict]:
        return self._run(lambda library: library.labels())

    def rename_label(self, word: str, called: str) -> dict:
        said = self._run(lambda library: library.rename_label(str(word), str(called)))
        if self._product is not None:
            self._product.rank_soon()
        return said

    def forget_label(self, word: str) -> dict:
        return self._run(lambda library: library.forget_label(str(word)))

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

    def maybe_same(self) -> list[dict]:
        return self._run(lambda library: library.maybe_same())

    def same_people(self, a: str, b: str, called: str) -> dict:
        said = self._run(lambda library: library.same_people(str(a), str(b), str(called)))
        if self._product is not None:
            self._product.rank_soon()
        return said

    def keep_apart(self, a: str, b: str) -> dict:
        said = self._run(lambda library: library.keep_apart(str(a), str(b)))
        if self._product is not None:
            self._product.rank_soon()
        return said

    def unname_since(self, since: int, until: int | None = None) -> dict:
        said = self._run(lambda library: library.unname_since(int(since), until))
        if self._product is not None:
            self._product.rank_soon()
        return said

    def unname_person(self, exemplar: str) -> dict:
        said = self._run(lambda library: library.unname_person(str(exemplar)))
        if self._product is not None:
            self._product.rank_soon()
        return said

    def name_person(self, exemplar: str, called: str) -> dict:
        said = self._run(lambda library: library.name_person(str(exemplar), str(called)))
        # The groups rewrite around the new name on the rank lane.
        self._product.rank_soon()
        return said

    # ---- rank ----

    def rank(self, n: int = 9, view: dict | None = None, avoid: list[str] | None = None,
             mode: str = "learn") -> dict:
        space = self._product.spaced()
        return self._run(lambda library: library.rank(
            int(n), view, list(avoid or []), mode=str(mode or "learn"), space=space))

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
              roll: str = "", rolls: dict | None = None, include_culled: bool = False) -> dict:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.bring(str(source), list(keys), str(kind),
                                              clear_source=bool(clear_source), roll=str(roll or ""),
                                              rolls={str(k): str(v) for k, v in (rolls or {}).items()},
                                              include_culled=bool(include_culled)))

    def intake_status(self) -> dict:
        status = self._product.intake_status() if self._product else {"phase": "idle"}
        # The window polls this once a second while a card comes in; the
        # taskbar button carries the same progress.
        running = status.get("phase") == "bringing"
        self._taskbar.show(int(status.get("done") or 0), int(status.get("total") or 0) if running else 0)
        return status

    def stop_intake(self) -> None:
        if self._product:
            self._product.stop_intake()

    def thumb(self, source: str, key: str) -> str | None:
        if self._product is None:
            raise RuntimeError("Choose where Azimuth should live first.")
        return self._wait(self._product.thumb(str(source), str(key)))

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
        # The chooser opens where the last export went: one less walk.
        chosen = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=self._exported_to if self._exported_to and os.path.isdir(self._exported_to) else "")
        if not chosen:
            return {"chosen": False}
        self._exported_to = str(chosen[0])
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
            self._taskbar.show(0, 0)
            if self._product is not None:
                self._wait(self._product.close())
            self._closed = True


def main() -> int:
    if not one_at_a_time():
        return 0
    keep_a_log(home.current())
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
