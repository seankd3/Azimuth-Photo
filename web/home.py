"""Where Azimuth lives: one folder holding the catalog and the previews.

Like a Lightroom catalog, the home is a folder the owner chooses once -- on
the largest fast local disk, by default -- and everything the application
makes for itself lives under it: the catalog, the previews, later the logs.
Nothing of the product's is written anywhere else. The one exception is the
pointer that remembers which home to open, which has to live where the
application can find it before a home exists.

``AZIMUTH_HOME``, when set, *is* the home, and no pointer is read or written;
that is how proofs and tests stay isolated from the owner's machine.
"""

from __future__ import annotations

import os
import shutil
import sys

APP = "Azimuth Photo"
CATALOG = os.path.join("catalog", "azimuth.db")
PREVIEWS = "previews"
ENV = "AZIMUTH_HOME"


def pointer() -> str:
    """The one per-user file that names the home."""

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, APP, "home")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "azimuth-photo", "home")


def current() -> str | None:
    """The home to open, or None when the owner has not chosen one yet."""

    forced = os.environ.get(ENV, "").strip()
    if forced:
        return os.path.abspath(forced)
    try:
        with open(pointer(), encoding="utf-8") as handle:
            named = handle.read().strip()
    except FileNotFoundError:
        return None
    return os.path.abspath(named) if named else None


def remember(path: str) -> str:
    """Make `path` the home opened from now on. Returns it, absolute."""

    path = os.path.abspath(os.fspath(path))
    if not os.environ.get(ENV, "").strip():
        os.makedirs(os.path.dirname(pointer()), exist_ok=True)
        with open(pointer(), "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
    return path


def propose() -> str:
    """A good home when none has been chosen: an `Azimuth Photo` folder on the
    fast local disk with the most room.

    Fast means an internal solid-state disk. Windows reports a USB archive
    drive as "fixed" too, and it is often the one with the most room, so the
    bus and the seek penalty are asked rather than the drive type alone; a
    catalog on a spinning USB disk is the slowest thing this product can do.
    """

    roots = [os.path.expanduser("~")]
    if sys.platform.startswith("win"):
        import ctypes

        fixed = 3  # DRIVE_FIXED
        candidates = [
            f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"
            if ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\") == fixed
        ]
        roots = [root for root in candidates if _fast(root)] or candidates or roots
    best = max(roots, key=lambda root: _free(root))
    return os.path.join(best, APP)


def paths(home: str) -> tuple[str, str]:
    """The catalog file and the previews folder inside one home."""

    home = os.path.abspath(os.fspath(home))
    return os.path.join(home, CATALOG), os.path.join(home, PREVIEWS)


def _free(root: str) -> int:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return -1


def _fast(root: str) -> bool:
    """Windows only: is the volume at `root` on an internal solid-state disk?
    Unknown counts as yes; USB, FireWire, or a seek penalty counts as no."""

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    handle = kernel.CreateFileW(f"\\\\.\\{root[0]}:", 0, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value or handle == -1:
        return True

    class Query(ctypes.Structure):
        _fields_ = [("property_id", ctypes.c_ulong), ("query_type", ctypes.c_ulong),
                    ("additional", ctypes.c_ubyte)]

    ioctl_storage_query_property = 0x2D1400
    out = ctypes.create_string_buffer(1024)
    returned = wintypes.DWORD()
    try:
        fast = True
        device = Query(0, 0, 0)  # StorageDeviceProperty: bus type at byte 28
        if kernel.DeviceIoControl(handle, ioctl_storage_query_property, ctypes.byref(device),
                                  ctypes.sizeof(device), out, 1024, ctypes.byref(returned), None):
            bus = out.raw[28]
            if bus in (4, 7):  # 1394, USB
                fast = False
        seek = Query(7, 0, 0)  # StorageDeviceSeekPenaltyProperty: flag at byte 8
        if fast and kernel.DeviceIoControl(handle, ioctl_storage_query_property, ctypes.byref(seek),
                                           ctypes.sizeof(seek), out, 1024, ctypes.byref(returned), None):
            fast = out.raw[8] == 0
        return fast
    finally:
        kernel.CloseHandle(handle)
