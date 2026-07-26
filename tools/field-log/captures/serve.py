#!/usr/bin/env python3
"""Boot a commit, scan the sample set, warm previews, then hold the server up so
it can be driven interactively (Browser pane) for feature shots like Develop/Film.

Usage: serve.py <label> <commit> <port>
"""
import sys, time
from harness import (ensure_worktree, start_server, wait_http, scan_and_wait,
                     wait_ready, SAMPLE, SCRATCH)
import shutil

label = sys.argv[1] if len(sys.argv) > 1 else "SERVE"
commit = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
port = int(sys.argv[3]) if len(sys.argv) > 3 else 8890

wt = ensure_worktree(label, commit)
home = SCRATCH / label
if home.exists():
    shutil.rmtree(home, ignore_errors=True)
log = wt.parent.parent / "captures" / f"{label}.serve.log"
proc, logf = start_server(wt, port, home, {}, log)
base = f"http://127.0.0.1:{port}"
print(f"[serve] {label} @ {commit} on {base}  (log: {log})")
if not wait_http(base, "/", 90):
    print("[serve] did not come up"); sys.exit(1)
print("[serve] up; scanning sample set...")
scan_and_wait(base, SAMPLE, 240)
print("[serve] warming previews...")
wait_ready(base, 200)
print(f"[serve] READY — holding {base} (Ctrl-C to stop)")
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
finally:
    try: proc.terminate()
    except Exception: pass
