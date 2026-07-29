import os
from playwright.sync_api import sync_playwright
OUT=os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEVLOG")
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":900}, device_scale_factor=1.4, color_scheme="dark")
    pg=ctx.new_page()
    # main site top (nav with Field Log)
    pg.goto("http://127.0.0.1:8792/index.html", wait_until="load"); pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(OUT,"site-nav.png"))
    # the log page hero (brand-aligned)
    pg.goto("http://127.0.0.1:8792/log/index.html", wait_until="load"); pg.wait_for_timeout(1800)
    pg.screenshot(path=os.path.join(OUT,"site-log-hero.png"))
    # a chapter to confirm accent recolor on demos
    el=pg.query_selector(".elo-demo"); el.scroll_into_view_if_needed(); pg.wait_for_timeout(1200)
    el.screenshot(path=os.path.join(OUT,"site-log-elo.png"))
    print("done")
    ctx.close(); b.close()
