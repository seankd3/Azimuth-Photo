#!/usr/bin/env python3
"""Capture the Develop editor + Film panel from an already-warm HEAD server."""
import sys, os
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8890"
OUT = os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEV")
os.makedirs(OUT, exist_ok=True)

def shot(page, name):
    page.wait_for_timeout(1200)
    page.screenshot(path=os.path.join(OUT, name + ".png"), timeout=60000, animations="disabled")
    print("shot ->", name)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--force-color-profile=srgb"])
    ctx = b.new_context(viewport={"width": 1680, "height": 1050}, device_scale_factor=2, color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    # ensure grid has photos
    for _ in range(15):
        n = pg.evaluate("() => document.querySelectorAll('img[src*=\"thumb\"],img[src*=\"/api/\"]').length")
        if n >= 4: break
        pg.wait_for_timeout(1500)
    # focus first photo (click its tile)
    try:
        pg.locator("img[src*='thumb'], img[src*='/api/']").first.click(timeout=5000)
        pg.wait_for_timeout(1500)
    except Exception as e:
        print("tile click warn:", e)
    # open the Develop lens
    opened = False
    for getter in (
        lambda: pg.get_by_role("tab", name="Develop", exact=False),
        lambda: pg.locator("[aria-label*='Develop']"),
        lambda: pg.get_by_text("Develop", exact=False),
    ):
        try:
            loc = getter().first
            if loc.count() > 0:
                loc.click(timeout=4000); opened = True; break
        except Exception:
            continue
    print("develop opened:", opened)
    # give the WebGL base decode + panels time
    pg.wait_for_timeout(6000)
    # wait for a canvas to exist
    for _ in range(12):
        if pg.evaluate("() => !!document.querySelector('canvas')"): break
        pg.wait_for_timeout(1000)
    shot(pg, "develop")
    # try to open the Film panel/section
    filmed = False
    for getter in (
        lambda: pg.get_by_role("button", name="Film", exact=False),
        lambda: pg.get_by_text("Film", exact=True),
        lambda: pg.get_by_text("Film", exact=False),
    ):
        try:
            loc = getter().first
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=3000); filmed = True; break
        except Exception:
            continue
    print("film opened:", filmed)
    pg.wait_for_timeout(3000)
    shot(pg, "film")
    ctx.close(); b.close()
print("done")
