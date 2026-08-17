"""Adopt V1's previews, then embed the library. Resumable; safe to re-run.

Written to be left alone for hours. Every unit of work is recorded in `cache`
as it completes, so killing this at any moment loses at most one photograph and
starting it again picks up where it stopped.

    python scripts/backfill.py            # adopt, then embed until done
    python scripts/backfill.py --adopt    # adoption only
"""

from __future__ import annotations

import os
import sys
import time

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
sys.path.insert(0, WEB)
os.environ.setdefault("AZIMUTH_DATA_DIR", r"C:\Azimuth Photo\data")
os.environ.setdefault("HF_HOME", r"D:\azimuth-bench\models")


def say(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


def main() -> int:
    from core.catalog_path import catalog_path
    from data import connection
    from model import cache, photos
    import render
    import search
    import tiles
    import work

    path = catalog_path()
    conn = connection.open_sync(path)
    conn.row_factory = __import__("sqlite3").Row
    say(f"catalog {path}")

    started = time.perf_counter()
    tally = tiles.adopt_v1(conn)
    say(f"adopted V1 previews: {tally} in {time.perf_counter() - started:.0f}s")
    if "--adopt" in sys.argv:
        return 0

    model = search.active_model()
    recipe = {"model": model}
    say(f"model {model}")
    say(f"owed {work.owing(conn, search.EMBEDDING, recipe=recipe):,}")

    done = failed = skipped = 0
    started = time.perf_counter()
    while True:
        rows = work.owed(conn, search.EMBEDDING, recipe=recipe, limit=400)
        if not rows:
            say("nothing owed; done")
            break
        progressed = False
        for row in rows:
            # A rendition on the laptop is the fast path and the offline one:
            # 6.00 img/s against 0.56, and no archive drive needed.
            source = tiles.path_for(row["hash"], render.LOUPE)
            if not os.path.exists(source):
                source = tiles.path_for(row["hash"], render.GRID)
            if not os.path.exists(source):
                source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
            if not source:
                skipped += 1
                continue
            try:
                made = cache.make(conn, row["hash"], search.EMBEDDING, source, recipe)
                progressed = True
                done += 1 if made else 0
                failed += 0 if made else 1
            except Exception as error:  # keep going; the row records the reason
                failed += 1
                say(f"  {type(error).__name__}: {error}"[:160])
            if (done + failed) % 200 == 0 and done:
                rate = done / max(time.perf_counter() - started, 1e-9)
                left = work.owing(conn, search.EMBEDDING, recipe=recipe)
                say(f"{done:,} embedded  {failed} failed  {skipped} unreachable"
                    f"  {rate:.2f} img/s  ~{left / max(rate, 1e-9) / 3600:.1f} h left")
        if not progressed:
            say(f"stalled: {skipped:,} unreachable (archive drive unplugged?)")
            break
    say(f"finished: {done:,} embedded, {failed} failed, {skipped} unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
