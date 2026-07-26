#!/usr/bin/env python3
"""Period-accurate capture harness for the Azimuth devlog.

Boots a historical commit of the photo app in isolation, scans a fixed sample
photo folder, and screenshots UI routes with Playwright. Never touches prod:
each era runs in its own git worktree with a scratch data dir on a scratch port.

Usage:
  harness.py --label M9 --commit HEAD --routes "grid=/" --port 8809
Config for the fancier interaction shots lives in per-era driver scripts that
import capture_era().
"""
import argparse, os, sys, time, subprocess, shutil, json, urllib.request, urllib.error, signal
from pathlib import Path

SB = Path(__file__).resolve().parents[1]
REPO = SB / "repo" / "azimuth-photo"
SAMPLE = SB / "sample-photos"
WORKTREES = SB / "_worktrees"
SCRATCH = SB / "_scratch"
PYEXE = SB / ".capture-venv" / "Scripts" / "python.exe"

def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)

def ensure_worktree(label, commit):
    """Create (or reuse) a detached worktree at the given commit."""
    wt = WORKTREES / label
    sh(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)])
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    sh(["git", "-C", str(REPO), "worktree", "prune"])
    WORKTREES.mkdir(parents=True, exist_ok=True)
    r = sh(["git", "-C", str(REPO), "worktree", "add", "--detach", str(wt), commit])
    if r.returncode != 0:
        print("worktree add failed:", r.stderr); sys.exit(2)
    return wt

def wait_http(base, path="/", timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(base + path, timeout=3) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.7)
    return False

def post_json(url, obj, timeout=30):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)

def get_json(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"_error": str(e)}

def warm_legacy_thumbs(base, n=80):
    """Older eras generate thumbnails on demand. Force-warm a range of ids across
    sizes so the grid renders instead of showing an empty state."""
    import concurrent.futures as cf
    def hit(url):
        try:
            urllib.request.urlopen(url, timeout=8).read()
            return True
        except Exception:
            return False
    urls = []
    for i in range(1, n + 1):
        for size in ("md", "lg", "sm"):
            urls.append(f"{base}/api/thumb/{size}/{i}")
    ok = 0
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(hit, urls):
            ok += 1 if r else 0
    print(f"  warmed legacy thumbs: {ok}/{len(urls)} requests ok")

def wait_ready(base, timeout=200):
    """Kick thumbnail pregen and wait until the grid reveals photos (preview-ready gate)."""
    post_json(base + "/api/cache/pregen/start", {})
    t0 = time.time()
    prev = None
    errs = 0
    while time.time() - t0 < timeout:
        r = get_json(base + "/api/rankings?limit=4")
        if isinstance(r, dict) and r.get("_error"):
            errs += 1
            if errs >= 3:
                # older era without this ready contract: fixed settle, let
                # capture_routes' wait-for-photos handle on-the-fly thumbs
                print("  (no rankings-ready contract; fixed settle)")
                time.sleep(8)
                return -1
            time.sleep(2); continue
        if "visible_images" not in r:
            print("  (rankings shape differs; warming legacy thumbnails)")
            warm_legacy_thumbs(base, n=80)
            time.sleep(6)
            return -1
        vis = r.get("visible_images", 0) if isinstance(r, dict) else 0
        pend = r.get("pending_thumbnails", 0) if isinstance(r, dict) else 0
        cur = (vis, pend)
        if cur != prev:
            print(f"  ready poll: visible={vis} pending={pend}")
            prev = cur
        # done when photos are visible and nearly all previews are built
        if vis and vis > 0 and pend <= 2:
            time.sleep(2)
            return vis
        if int(time.time() - t0) % 12 == 0:
            post_json(base + "/api/cache/pregen/start", {})
        time.sleep(2)
    print(f"  ready wait timed out ({prev})")
    return prev[0] if prev else 0

def scan_and_wait(base, folder, timeout=240):
    st, body = post_json(base + "/api/scan", {"folder": str(folder)})
    print(f"  scan POST -> {st} {body[:160]}")
    t0 = time.time()
    last = None
    stable_ins = 0
    prev_ins = -1
    while time.time() - t0 < timeout:
        s = get_json(base + "/api/scan/status")
        last = s
        if s.get("_error"):
            time.sleep(1)
            if time.time() - t0 > 15:
                break
            continue
        # normal completion
        if s.get("done") or s.get("state") in ("done", "idle", "complete"):
            print(f"  scan done: {json.dumps(s)[:180]}")
            return s
        # some eras hang in post-insert prefetch with scanning stuck True, but the
        # rows are already committed and the event loop stays responsive. Exit once
        # the insert count is > 0 and has held steady for a few polls.
        ins = s.get("total_inserted", 0)
        if ins and ins == prev_ins:
            stable_ins += 1
            if stable_ins >= 3:
                print(f"  scan settled (inserted={ins}, scanning flag ignored)")
                return s
        else:
            stable_ins = 0
        prev_ins = ins
        # scanning explicitly false with nothing pending
        if s.get("scanning") is False and ins:
            print(f"  scan done: {json.dumps(s)[:180]}")
            return s
        time.sleep(1.5)
    print(f"  scan wait ended: {json.dumps(last)[:180] if last else None}")
    return last

def start_server(wt, port, home, extra_env=None, log_path=None, server_py=None):
    env = dict(os.environ)
    env["AZIMUTH_HOME"] = str(home)
    env["AZIMUTH_THUMB_CACHE_DIR"] = str(home / "thumbs")
    env["AZIMUTH_DEVELOP_CACHE_DIR"] = str(home / "develop")
    env["AZIMUTH_MODELS_DIR"] = str(home / "models")
    env["AZIMUTH_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    if extra_env:
        env.update(extra_env)
    home.mkdir(parents=True, exist_ok=True)
    (home / "thumbs").mkdir(exist_ok=True)
    (home / "develop").mkdir(exist_ok=True)
    logf = open(log_path, "w") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(
        [str(server_py or PYEXE), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(wt / "web"), env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    return proc, logf

def stop_server(proc, logf):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try: proc.kill()
        except Exception: pass
    try:
        if logf not in (None, subprocess.DEVNULL): logf.close()
    except Exception: pass

def capture_routes(base, shots, outdir, viewport=(1440, 900), settle=2.5):
    from playwright.sync_api import sync_playwright
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        for shot in shots:
            name = shot["name"]
            vp = shot.get("viewport", viewport)
            ctx = browser.new_context(viewport={"width": vp[0], "height": vp[1]},
                                      device_scale_factor=shot.get("dsf", 2),
                                      color_scheme="dark")
            page = ctx.new_page()
            url = base + shot["path"]
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"  [{name}] goto warn: {e}")
            page.wait_for_timeout(int(settle * 1000))
            if shot.get("wait_photos", True):
                # wait for real photo thumbnails to render; reload once if the SPA
                # cached an empty pre-ready result
                reloaded = False
                for _ in range(20):
                    n = page.evaluate(
                        "() => document.querySelectorAll('img[src*=\"thumb\"], img[src*=\"/api/\"], .grid img, [class*=cell] img').length"
                    )
                    if n and n >= 3:
                        break
                    page.wait_for_timeout(1500)
                    if not reloaded and _ >= 3:
                        try: page.reload(wait_until="domcontentloaded")
                        except Exception: pass
                        reloaded = True
                        page.wait_for_timeout(2500)
                page.wait_for_timeout(2000)  # let thumbnails decode/paint
            for step in shot.get("steps", []):
                try:
                    step(page)
                except Exception as e:
                    print(f"  [{name}] step warn: {e}")
            page.wait_for_timeout(int(shot.get("post_settle", 1.5) * 1000))
            out = outdir / f"{name}.png"
            page.screenshot(path=str(out), full_page=shot.get("full_page", False))
            print(f"  shot -> {out.name}")
            results.append(str(out))
            ctx.close()
        browser.close()
    return results

def capture_era(label, commit, shots, port=8809, smoke=True, extra_env=None, scan_timeout=240, ready_timeout=200, server_py=None):
    print(f"== capture {label} @ {commit} ==")
    wt = ensure_worktree(label, commit)
    home = SCRATCH / label
    if home.exists(): shutil.rmtree(home, ignore_errors=True)
    env = {"AZIMUTH_SMOKE_MODE": "1"} if smoke else {}
    if extra_env: env.update(extra_env)
    log_path = SB / "captures" / f"{label}.server.log"
    proc, logf = start_server(wt, port, home, env, log_path, server_py=server_py)
    base = f"http://127.0.0.1:{port}"
    try:
        if not wait_http(base, "/", timeout=60):
            print(f"  !! server did not come up; see {log_path}")
            return False
        print("  server up")
        scan_and_wait(base, SAMPLE, timeout=scan_timeout)
        wait_ready(base, timeout=ready_timeout)
        capture_routes(base, shots, SB / "captures" / label)
        return True
    finally:
        stop_server(proc, logf)
        print("  server stopped")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--routes", default="hero=/")
    ap.add_argument("--port", type=int, default=8809)
    ap.add_argument("--no-smoke", action="store_true")
    args = ap.parse_args()
    shots = []
    for pair in args.routes.split(";"):
        nm, _, pth = pair.partition("=")
        shots.append({"name": nm, "path": pth or "/"})
    ok = capture_era(args.label, args.commit, shots, port=args.port, smoke=not args.no_smoke)
    sys.exit(0 if ok else 1)
