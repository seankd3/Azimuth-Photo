import os
from PIL import Image
from playwright.sync_api import sync_playwright
OUT=os.environ.get("AZIMUTH_CAPTURE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEVLOG")
BASE="http://127.0.0.1:8791/index.html"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1400,"height":900}, device_scale_factor=1, color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE, wait_until="load"); pg.wait_for_timeout(1500)
    # reveal all sections by scrolling to bottom first, then top
    pg.evaluate("""async()=>{for(let y=0;y<document.body.scrollHeight;y+=700){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,60));}window.scrollTo(0,0);}""")
    pg.wait_for_timeout(800)
    m=pg.query_selector("#method"); m.scroll_into_view_if_needed(); pg.wait_for_timeout(900)
    m.screenshot(path=os.path.join(OUT,"f-method.png")); print("method shot")
    # full page tall
    pg.evaluate("window.scrollTo(0,0)"); pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT,"fullpage.png"), full_page=True)
    im=Image.open(os.path.join(OUT,"fullpage.png"))
    print("fullpage", im.size)
    # downscale tall for review
    im2=im.resize((520, round(im.height*520/im.width)), Image.LANCZOS)
    im2.save(os.path.join(OUT,"fullpage-thumb.png"))
    print("thumb", im2.size)
    ctx.close(); b.close()
