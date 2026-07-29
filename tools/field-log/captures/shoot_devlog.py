import os
from playwright.sync_api import sync_playwright
OUT=os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEVLOG")
os.makedirs(OUT, exist_ok=True)
BASE="http://127.0.0.1:8791/index.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":940}, device_scale_factor=1.5, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1500)
    # hero
    pg.screenshot(path=os.path.join(OUT,"01-hero.png"))
    # scroll through and grab a few full-viewport shots at chapter anchors
    for anchor,name in [("#ch-genesis","02-genesis"),("#ch-speed","03-speed"),
                        ("#ch-darkroom","04-darkroom"),("#wins","05-wins"),
                        ("#stories","06-stories"),("#ch-fleet","07-fleet")]:
        pg.evaluate(f"document.querySelector('{anchor}').scrollIntoView()")
        pg.wait_for_timeout(1400)
        pg.screenshot(path=os.path.join(OUT,name+".png"))
    # full page tall shot (may be huge) - skip, do a mid reveal check
    print("shots done:", os.listdir(OUT))
    ctx.close(); b.close()
