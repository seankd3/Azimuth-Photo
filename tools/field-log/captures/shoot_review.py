import os
from playwright.sync_api import sync_playwright
OUT=r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEVLOG"
BASE="http://127.0.0.1:8791/index.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    # desktop review
    ctx=b.new_context(viewport={"width":1440,"height":940}, device_scale_factor=1.4, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1200)
    for sel,name in [(".race","r-race"),(".code","r-code"),(".ledger","r-ledger"),(".fleet","r-fleet"),(".foot","r-foot")]:
        el=pg.query_selector(sel)
        if el: el.scroll_into_view_if_needed(); pg.wait_for_timeout(900); el.screenshot(path=os.path.join(OUT,name+".png")); print("shot",name)
    ctx.close()
    # mobile review
    m=b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2, color_scheme="dark")
    mp=m.new_page(); mp.goto(BASE, wait_until="load"); mp.wait_for_timeout(1200)
    mp.screenshot(path=os.path.join(OUT,"m-hero.png"))
    mp.query_selector("#ch-darkroom").scroll_into_view_if_needed(); mp.wait_for_timeout(1000)
    mp.screenshot(path=os.path.join(OUT,"m-darkroom.png"))
    print("mobile done")
    m.close(); b.close()
