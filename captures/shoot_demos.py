import os
from playwright.sync_api import sync_playwright
OUT=r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEVLOG"
BASE="http://127.0.0.1:8791/index.html"
sels=[(".elo-demo","d-elo"),(".race","d-race"),(".twin-demo","d-twin"),(".hala-demo","d-hala")]
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1120,"height":820}, device_scale_factor=1.5, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1200)
    for sel,name in sels:
        el=pg.query_selector(sel)
        if not el: print("missing",sel); continue
        el.scroll_into_view_if_needed(); pg.wait_for_timeout(1600)
        el.screenshot(path=os.path.join(OUT,name+".png"))
        print("shot",name)
    ctx.close(); b.close()
