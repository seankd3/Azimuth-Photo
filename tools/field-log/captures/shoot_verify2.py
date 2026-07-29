import os
from playwright.sync_api import sync_playwright
OUT=os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEVLOG")
BASE="http://127.0.0.1:8791/index.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":940}, device_scale_factor=1.4, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1400)
    for sel,name in [("#ch-mind","v-mind"),("#ch-azimuth","v-credit"),("#ch-fleet","v-fleet")]:
        el=pg.query_selector(sel); el.scroll_into_view_if_needed(); pg.wait_for_timeout(1100); el.screenshot(path=os.path.join(OUT,name+".png")); print(name)
    # develop plate specifically
    for sel,name in [(".plate img[src*='09-develop']","v-develop"),(".plate img[src*='10-film']","v-film")]:
        el=pg.query_selector(sel)
        if el: el.scroll_into_view_if_needed(); pg.wait_for_timeout(900); el.screenshot(path=os.path.join(OUT,name+".png")); print(name)
    ctx.close(); b.close()
