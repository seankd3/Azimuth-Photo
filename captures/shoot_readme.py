import os
from playwright.sync_api import sync_playwright
OUT=r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEVLOG"
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":940,"height":1100}, device_scale_factor=1.4)
    pg=ctx.new_page(); pg.goto("http://127.0.0.1:8793/readme-preview.html", wait_until="load"); pg.wait_for_timeout(1000)
    pg.screenshot(path=os.path.join(OUT,"mk-readme-top.png"))  # hero + callouts
    # scroll to Develop section
    pg.evaluate("() => {const h=[...document.querySelectorAll('h2')].find(e=>e.textContent.includes('darkroom')); if(h) h.scrollIntoView();}")
    pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(OUT,"mk-readme-develop.png"))
    print("done")
    ctx.close(); b.close()
