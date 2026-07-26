import os
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8890"; OUT=r"C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/captures/DEV"
os.makedirs(OUT, exist_ok=True)
def grab(pg, tabname, out):
    ok=False
    for g in (lambda: pg.get_by_role("tab", name=tabname, exact=False),
              lambda: pg.locator(f"[aria-label*='{tabname}']")):
        try:
            loc=g().first
            if loc.count()>0: loc.click(timeout=4000); ok=True; break
        except Exception: pass
    pg.wait_for_timeout(4500)
    pg.screenshot(path=os.path.join(OUT,out+".png")); print(out,"opened=",ok)
with sync_playwright() as p:
    b=p.chromium.launch(args=["--force-color-profile=srgb"])
    ctx=b.new_context(viewport={"width":1680,"height":1050},device_scale_factor=2,color_scheme="dark")
    pg=ctx.new_page(); pg.goto(BASE+"/",wait_until="domcontentloaded"); pg.wait_for_timeout(4000)
    grab(pg,"Map","lens-map")
    grab(pg,"People","lens-people")
    grab(pg,"Timeline","lens-timeline")
    ctx.close(); b.close()
print("done")
