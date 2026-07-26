#!/usr/bin/env python3
"""Capture a develop 'look applied' shot in a fresh, GPU-stable browser."""
import os
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8890"
OUT = r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEV"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--enable-unsafe-swiftshader", "--force-color-profile=srgb"])
    ctx = b.new_context(viewport={"width": 1680, "height": 1050}, device_scale_factor=2, color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    for _ in range(12):
        if pg.evaluate("() => document.querySelectorAll('img[src*=\"thumb\"],img[src*=\"/api/\"]').length") >= 4:
            break
        pg.wait_for_timeout(1200)
    # open a dramatic launch/rocket frame (a later tile) -> develop. ONE render,
    # ONE screenshot: the preset-apply path crashes headless WebGL, a clean render
    # does not.
    tiles = pg.locator("img[src*='thumb'], img[src*='/api/']")
    n = tiles.count()
    pick = min(9, max(0, n - 1))  # a rocket/launch frame further into the grid
    try:
        tiles.nth(pick).click(timeout=5000)
        pg.wait_for_timeout(1200)
    except Exception as e:
        print("tile warn", e)
    for g in (lambda: pg.get_by_role("tab", name="Develop", exact=False),
              lambda: pg.locator("[aria-label*='Develop']")):
        try:
            loc = g().first
            if loc.count() > 0: loc.click(timeout=4000); break
        except Exception: pass
    pg.wait_for_timeout(13000)
    pg.screenshot(path=os.path.join(OUT, "film.png"), timeout=60000, animations="disabled")
    print("develop-2 shot ok")
    ctx.close(); b.close()
