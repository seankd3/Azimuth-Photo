import os
from playwright.sync_api import sync_playwright
OUT=r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEVLOG"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":900}, device_scale_factor=1.4, color_scheme="dark")
    pg=ctx.new_page()
    # website story band
    pg.goto("http://127.0.0.1:8792/index.html", wait_until="load"); pg.wait_for_timeout(1200)
    el=pg.query_selector("#story"); el.scroll_into_view_if_needed(); pg.wait_for_timeout(1200)
    el.screenshot(path=os.path.join(OUT,"mk-story.png")); print("story")
    # dev-log closing CTA
    pg.goto("http://127.0.0.1:8792/log/index.html", wait_until="load"); pg.wait_for_timeout(1200)
    el=pg.query_selector(".endcta"); el.scroll_into_view_if_needed(); pg.wait_for_timeout(1000)
    el.screenshot(path=os.path.join(OUT,"mk-endcta.png")); print("endcta")
    ctx.close(); b.close()
