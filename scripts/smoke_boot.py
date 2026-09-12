#!/usr/bin/env python3
"""The one proof a build must pass before it lands: the app opens a small
library and draws it, and the window reports no error.

    web\\.venv\\Scripts\\python.exe scripts\\smoke_boot.py

Makes a home in a temp folder with a few photographs, opens the built
document over it exactly as the desktop does (scripts/native_proof.py), and
asks the page how many cells the grid drew and what errors it caught. Exit 0
only when the grid shows every photograph and nothing was thrown. A bundle
that passes the linter and the suite once drew nothing at all for an hour;
this is the check that would have refused it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PHOTOS = 4
ASKS = 6            # the probe, asked every two seconds after four: a slow machine gets sixteen

PROBE = """
(() => JSON.stringify({
  cells: document.querySelectorAll('.photo-cell').length,
  count: document.querySelector('[data-photo-count]')?.textContent || '',
  errors: window.__azimuthErrors || [],
}))()
"""


def make_home(folder: Path) -> Path:
    """A home with a library of a few photographs, swept and tiled."""

    from PIL import Image

    sys.path.insert(0, str(WEB))
    os.environ["AZIMUTH_HOME"] = str(folder)
    import boot
    import metadata
    import work

    photos = folder / "Photos" / "2026" / "2026-09-12"
    photos.mkdir(parents=True)
    for i in range(PHOTOS):
        Image.new("RGB", (600, 400), (30 * i, 90, 140)).save(photos / f"smoke-{i}.jpg", "JPEG")
    with boot.Library(str(folder / "catalog" / "azimuth.db"), str(folder / "previews")) as product:
        drive = product.attach(str(folder / "Photos"))
        product.refresh(drive["uuid"])
        # The kinds the app's own worker steps first, in its order: the
        # metadata, then the tiles. The heavy kinds want models the smoke
        # does not load.
        while work.step(product.conn, (metadata.KIND, *product.tiles.kinds), yield_to=lambda: False):
            pass
    return folder


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="azimuth-smoke-", ignore_cleanup_errors=True) as made:
        home = make_home(Path(made))
        probe = home / "probe.js"
        probe.write_text(PROBE, encoding="utf-8")
        out = home / "smoke.png"
        said = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "native_proof.py"), str(home), str(out),
             *(("--probe", str(probe)) * ASKS), "--wait", "4", "--gap", "2"],
            capture_output=True, text=True, timeout=180, cwd=ROOT, check=False,
        )
    # An answer is a JSON string; a probe that failed prints "PROBE failed: ...".
    answers = [json.loads(json.loads(line[len("PROBE "):]))
               for line in said.stdout.splitlines() if line.startswith('PROBE "')]
    if not answers:
        print(said.stdout[-2000:], said.stderr[-2000:], file=sys.stderr)
        print("smoke: the window never answered", file=sys.stderr)
        return 1
    # The first answer with the whole grid drawn, else the last: a slow
    # machine takes longer to draw. The page keeps every fault it caught,
    # so the last answer holds them all.
    answer = next((a for a in answers if a["cells"] == PHOTOS), answers[-1])
    errors = answers[-1]["errors"]
    ok = answer["cells"] == PHOTOS and not errors
    print(f"smoke: {answer['cells']} of {PHOTOS} cells, {answer['count']!r}, errors {errors}")
    print("smoke: the build opens" if ok else "smoke: REFUSED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
