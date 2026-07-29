import os
from playwright.sync_api import sync_playwright
OUT=os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEVLOG")
BASE="http://127.0.0.1:8791/index.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":940}, device_scale_factor=1.4, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1200)
    for sel,name in [(".race","f-race"),("#ch-web","f-web"),(".hero .statgrid","f-stats")]:
        el=pg.query_selector(sel)
        if el: el.scroll_into_view_if_needed(); pg.wait_for_timeout(1300); el.screenshot(path=os.path.join(OUT,name+".png")); print("shot",name)
    ctx.close(); b.close()
