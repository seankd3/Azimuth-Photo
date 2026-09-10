#!/usr/bin/env python3
"""The UI in a browser tab, with no window and no catalog.

The desktop document is one file that talks to Python through one bridge, so
a page that supplies a fake bridge runs the whole UI anywhere a browser does.
This builds that page from the current bundle and serves it:

    python scripts/harness.py            # build/harness.html on :8765
    python scripts/harness.py --build    # only write the page

Open it, drive it, read its DOM. The fake library is 124 photographs over 27
days of one year with one four-frame stack, enough for every grid, chapter,
timeline and selection behaviour; extend `STUB` when a surface needs more.
Tiles are absent on purpose (cells show their honest pending state), so the
harness proves shape and behaviour, never pixels -- `scripts/native_proof.py`
does pixels.
"""

from __future__ import annotations

import http.server
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "build" / "desktop" / "index.html"
OUTPUT = ROOT / "build" / "harness.html"
PORT = 8765

STUB = r"""<script>
(() => {
  const N = 124;
  const dates = ['2026-09-08','2026-09-07','2026-09-06','2026-09-05','2026-09-01','2026-08-27','2026-08-26',
    '2026-08-25','2026-08-23','2026-08-22','2026-08-15','2026-08-14','2026-08-05','2026-07-12','2026-07-11',
    '2026-06-05','2026-05-31','2026-05-27','2026-05-26','2026-05-21','2026-04-19','2026-04-18','2026-04-04',
    '2026-03-28','2026-03-08','2026-03-05','2026-01-11'];
  const days = [];
  const photos = [];
  let id = 1;
  for (const day of dates) {
    const count = day === dates[dates.length - 1] ? N - photos.length : Math.min(5, N - photos.length);
    days.push({ day, count });
    for (let i = 0; i < count; i += 1, id += 1) {
      photos.push({ id, hash: `h${id}`, tail: `Raws/Digital/2026/${day}/p${id}.cr3`, tile: null, loupe: null,
        width: 6000, height: 4000, status: 'unflagged', rotate: 0, stack: 0, stars: 0,
        reachable: true, tile_failed: false, date_taken: `${day} 12:00:00`, camera_model: 'EOS R5' });
    }
  }
  photos[0].stack = 3;
  const members = [1, 2, 3].map((n) => ({ ...photos[0], id: 1000 + n, stack: 0, stack_of: 1,
    tail: `Raws/Digital/2026/2026-09-08/m${n}.cr3` }));
  const shown = (view) => {
    const collapsed = view && view.collapsed;
    const open = collapsed ? (view.expanded || []).includes(1) : !((view && view.folded) || []).includes(1);
    return open ? [photos[0], ...members, ...photos.slice(1)] : photos;
  };
  const api = {
    home: () => 'C:/harness/home', propose_home: () => 'C:/harness/home', settle_home: (p) => p,
    counts: () => ({ photos: N, starred: 0, unidentified: 0, trash: 0 }),
    drives: () => [{ id: 1, uuid: 'u-1', root: 'C:/harness/photos', label: 'Harness', is_record: 0, attached: true, seen_at: 0 }],
    pulse: () => ({ done: 0, swept: 1, shaped: 0, cards: 0, doing: null, left: {} }),
    look: () => 0,
    photos: (sort, limit, offset, view) => (sort === 'oldest' ? [...shown(view)].reverse() : shown(view)).slice(offset, offset + limit),
    size: (view) => shown(view).length, folders: () => [], photo: (pid) => shown(null).find((p) => p.id === pid),
    stack: (ids) => ({ cover: ids[0], members: ids.slice(1) }), unstack: (ids) => ({ unstacked: ids }),
    trash_photos: () => [], trash_count: () => 0,
    days: (view) => days.map((d, i) => (i === 0 ? { ...d, count: d.count + shown(view).length - N } : d)),
    sessions: () => [],
    albums: () => [], cameras: () => [], facets: () => ({}), labels: () => [], cards: () => [],
    people: () => [{ term: 'Someone 1', count: 14, settled: false, person: 'h1:0', samples: [] }, { term: 'Ada', count: 40, settled: true, person: 'h2:0', samples: [] }],
    teach: (word, ids) => ({ word, taught: ids.length }), faces: () => [[0.4, 0.3, 0.2, 0.25]], rename_label: (word, called) => ({ id: 'l1', word: called }),
    forget_label: () => ({ id: 'l1' }), remember_album: () => true,
    freeze_album: () => ({ frozen: 3, criteria: [{ is: 'stars', least: 3 }] }), redefine_album: (id) => ({ id }),
    name_person: () => ({ named: 'Ada' }), unname_person: (e) => ({ unnamed: e }),
    maybe_same: () => [{ a: { exemplar: 'h1:0', term: 'Someone 1', settled: false, samples: [] }, b: { exemplar: 'h2:0', term: 'Ada', settled: true, samples: [] }, close: 0.44 }], same_people: (a, b, called) => ({ named: called }), keep_apart: (a, b) => ({ apart: `${a}|${b}` }),
    identifiers: () => photos.map((p) => p.id), search: () => ({ photos: [], total: 0 }),
    intake_status: () => ({ running: false }), forget_missing: () => ({}),
    rank: (n) => ({ photos: photos.slice(0, n), judged: 0, earned: 0, total: N }),
    round: () => ({ decision: 1 }), unround: () => ({}),
    pick: (ids) => ({ changed: ids.map((id) => ({ subject: `h${id}`, family: 'status', after: 'picked', photos: 1 })), unidentified: 0 }),
    clear_pick: (ids) => ({ changed: ids.map((id) => ({ subject: `h${id}`, family: 'status', after: null, photos: 1 })), unidentified: 0 }),
    reject: (ids) => ({ changed: ids.map((id) => ({ subject: `h${id}`, family: 'status', after: 'trashed', photos: 1 })), unidentified: 0 }),
    turn: (ids, by) => ({ changed: ids.map((id) => ({ subject: `h${id}`, family: 'rotate', after: by, photos: 1 })), unidentified: 0 }),
    undo_cull: () => ({}),
    develop_state: () => ({ plain: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', settings: {}, box: null }),
    develop: () => ({}),
  };
  window.__calls = [];
  window.pywebview = { api: new Proxy(api, { get: (target, name) => {
    // A promise resolved with an object that answers `then` treats it as a
    // thenable and never settles; the bridge must not answer everything.
    if (typeof name !== 'string' || name === 'then') return undefined;
    return (...args) => {
      window.__calls.push([performance.now() | 0, name, target[name] ? 'ok' : 'MISSING']);
      // A verb the stub lacks fails loud: a null that passed for an answer
      // let whole stages pass every headless run while broken.
      if (!target[name]) return Promise.reject(new Error(`the harness has no ${name}`));
      return Promise.resolve(target[name](...args));
    };
  } }) };
})();
</script>"""


def build() -> Path:
    if not DOCUMENT.is_file():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_desktop_ui.py")], check=True)
    document = DOCUMENT.read_text(encoding="utf-8")
    at = document.index("<script>")
    OUTPUT.write_text(document[:at] + STUB + document[at:], encoding="utf-8")
    return OUTPUT


def main() -> int:
    page = build()
    print(page)
    if "--build" in sys.argv[1:]:
        return 0
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(OUTPUT.parent), **k)  # noqa: E731
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
        print(f"http://127.0.0.1:{PORT}/harness.html", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
