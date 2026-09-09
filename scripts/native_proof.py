#!/usr/bin/env python3
"""The real app, off-screen: open it on an isolated home, ask the page a
question, capture its own window, close. Nothing touches the foreground.

    python scripts/native_proof.py <home> <out.png> [--probe probe.js] [--wait 25]

`home` is an `AZIMUTH_HOME` with a catalog already attached and swept (build
one with `boot.Library` first, or point at a proof home from an earlier run).
The window opens at x = -2400, beyond the left edge of any screen, and is
still rendered, so `PrintWindow` captures it; the probe is JavaScript
evaluated in the page whose JSON result is printed on one `PROBE` line.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def snap(hwnd: int, path: Path) -> tuple[int, int]:
    """PrintWindow into a file: what the window shows, wherever it is."""

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.r - rect.l, rect.b - rect.t
    hdc_win = user32.GetWindowDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc, bmp)
    user32.PrintWindow(hwnd, hdc, 2)  # PW_RENDERFULLCONTENT: WebView2 included
    info = _BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = w, -h, 1, 32
    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, bmp, 0, h, buffer, ctypes.byref(info), 0)
    from PIL import Image

    Image.frombuffer("RGBA", (w, h), buffer, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(hwnd, hdc_win)
    return w, h


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home")
    parser.add_argument("out")
    parser.add_argument("--probe", help="a JavaScript file evaluated in the page; its result is printed")
    parser.add_argument("--wait", type=float, default=25.0, help="seconds to let the library settle")
    args = parser.parse_args()

    os.environ["AZIMUTH_HOME"] = str(Path(args.home).resolve())
    # pywebview fixes its base path from the working directory at import, so
    # the import comes first and the directory never changes.
    import webview

    sys.path.insert(0, str(WEB))

    import desktop as edge
    import home

    product = edge.Desktop(home.current())
    window = webview.create_window(
        "Azimuth Photo proof",
        url=edge.bundled_document().as_uri(),
        js_api=product,
        width=1900, height=1150, x=-2400, y=60,
        background_color="#0a0c0e",
    )
    product.bind(window)
    window.events.closed += product.close
    probe = Path(args.probe).read_text(encoding="utf-8") if args.probe else None

    def run():
        time.sleep(args.wait)
        if probe:
            try:
                print("PROBE " + json.dumps(window.evaluate_js(probe)), flush=True)
            except Exception as error:  # noqa: BLE001 - the probe is the person's own script
                print(f"PROBE failed: {error!r}", flush=True)
        try:
            w, h = snap(int(window.native.Handle.ToInt64()), Path(args.out))
            print(f"SNAP {w}x{h} {args.out}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"SNAP failed: {error!r}", flush=True)
        window.destroy()

    threading.Thread(target=run, daemon=True).start()
    try:
        webview.start(gui="edgechromium")
    finally:
        product.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
