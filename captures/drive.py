#!/usr/bin/env python3
"""Drive period-accurate captures across milestone commits.

Each era defines: commit, hero route(s), viewport, and per-shot interaction steps.
Run one or more eras:  drive.py M1 M2 ...   (no args = all)
"""
import sys
from pathlib import Path
from harness import capture_era, SB

PORT_BASE = 8820
LEGACY_PY = SB / ".legacy-venv" / "Scripts" / "python.exe"
PY310 = SB / ".py310-venv" / "Scripts" / "python.exe"
LEGACY_ERAS = {"M1", "M2", "M3", "M4"}  # Jinja-template eras need early-2026 starlette
PY310_ERAS = {"M6", "M7"}  # darkroom eras: py3.12 sqlite trips their schema-transaction setup

def click_first_tile(page):
    for sel in [".grid img", "[class*=cell] img", ".mosaic img", ".justified img", "img[src*='thumb']", "img[src*='/api/']"]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                loc.first.click(timeout=4000)
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue

def press_key(k):
    def _step(page):
        page.keyboard.press(k)
        page.wait_for_timeout(2500)
    return _step

def wait_ms(ms):
    def _step(page):
        page.wait_for_timeout(ms)
    return _step

def click_text(text):
    def _step(page):
        for getter in (
            lambda: page.get_by_role("button", name=text, exact=False),
            lambda: page.get_by_text(text, exact=False),
        ):
            try:
                loc = getter().first
                if loc.count() > 0:
                    loc.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                continue
    return _step

# Milestone definitions. Hero route per era per the runbook.
ERAS = {
    "M1": dict(commit="60cb19f39", shots=[
        {"name": "index", "path": "/", "viewport": (1440, 900), "wait_photos": False},
        {"name": "compare", "path": "/compare", "viewport": (1440, 900)},
        {"name": "rankings", "path": "/rankings", "viewport": (1440, 900)},
    ]),
    "M2": dict(commit="c0d7f5441", shots=[
        {"name": "library", "path": "/library", "viewport": (1440, 900)},
        {"name": "compare", "path": "/", "viewport": (1440, 900)},
    ]),
    "M3": dict(commit="b28564460", shots=[
        {"name": "library", "path": "/library", "viewport": (1440, 900)},
        {"name": "loupe", "path": "/library", "viewport": (1440, 900),
         "steps": [click_first_tile], "wait_photos": True},
    ]),
    "M4": dict(commit="149d8ba55", shots=[
        {"name": "library", "path": "/library", "viewport": (1440, 900)},
    ]),
    "M5": dict(commit="9b444ff57", shots=[
        {"name": "desktop", "path": "/d", "viewport": (1600, 1000)},
    ]),
    "M6": dict(commit="874b8a705", shots=[
        {"name": "grid", "path": "/", "viewport": (1680, 1050)},
        {"name": "develop", "path": "/", "viewport": (1680, 1050),
         "steps": [click_first_tile, press_key("d"), wait_ms(4000)],
         "wait_photos": True, "post_settle": 5},
    ]),
    "M7": dict(commit="385db0b7c", shots=[
        {"name": "grid", "path": "/", "viewport": (1680, 1050)},
        {"name": "develop", "path": "/", "viewport": (1680, 1050),
         "steps": [click_first_tile, press_key("d"), wait_ms(4500)],
         "wait_photos": True, "post_settle": 5},
        {"name": "film", "path": "/", "viewport": (1680, 1050),
         "steps": [click_first_tile, press_key("d"), wait_ms(4500), click_text("Film"), wait_ms(2500)],
         "wait_photos": True, "post_settle": 4},
    ]),
    "M8": dict(commit="49823da21", shots=[
        {"name": "grid", "path": "/", "viewport": (1600, 1000)},
    ]),
    "M9": dict(commit="HEAD", shots=[
        {"name": "grid", "path": "/", "viewport": (1600, 1000)},
    ]),
}

def main():
    want = [a for a in sys.argv[1:] if a in ERAS] or list(ERAS.keys())
    for i, label in enumerate(want):
        cfg = ERAS[label]
        port = PORT_BASE + i
        print(f"\n########## {label} (port {port}) ##########")
        server_py = None
        if label in LEGACY_ERAS:
            server_py = str(LEGACY_PY)
        elif label in PY310_ERAS:
            server_py = str(PY310)
        try:
            capture_era(label, cfg["commit"], cfg["shots"], port=port,
                        smoke=False, ready_timeout=180, server_py=server_py)
        except Exception as e:
            print(f"  !! {label} failed: {e}")

if __name__ == "__main__":
    main()
