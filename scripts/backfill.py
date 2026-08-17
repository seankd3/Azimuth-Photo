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

    # Render the *grid* tile from an original, not the loupe one. The model
    # sees 384px and a grid tile is 400, so it is the whole of what the
    # embedding needs, it is the size the grid actually shows, and it costs a
    # fraction of a 1920px render off a RAW. Rendering the loupe size here was
    # measured at roughly 0.1 img/s -- a week for the archive.
    tile_recipe = {"size": render.GRID, "edits": None, "rotate": 0}
    done = failed = 0
    from_archive = 0
    unreachable: set[str] = set()
    started = time.perf_counter()

    while True:
        # Ask for more than we can use, because photographs we cannot reach stay
        # owed forever and would otherwise be handed back every pass. They are
        # skipped in memory rather than recorded as failures: "not on this
        # machine" is a fact about the machine, and writing it into the cache
        # would tell a future run with the drive plugged in not to bother.
        rows = [r for r in work.owed(conn, search.EMBEDDING, recipe=recipe, limit=4000)
                if r["hash"] not in unreachable]
        if not rows:
            say("nothing left that this machine can reach; done")
            break

        for row in rows:
            digest = row["hash"]
            source = tiles.path_for(digest, render.LOUPE)
            if not os.path.exists(source):
                source = tiles.path_for(digest, render.GRID)
            if not os.path.exists(source):
                # No rendition here, so the original has to be read. Render the
                # tile from it first and embed from that: the decode is the
                # expensive part and this way it is paid once for two answers,
                # and the photograph gets a preview that lives on the laptop.
                original = photos.locate(conn, row["tail"], expected_size=row["file_size"])
                if not original:
                    unreachable.add(digest)
                    continue
                from_archive += 1
                made = cache.make(conn, digest, "tile", original, tile_recipe)
                source = (made or {}).get("path") or original
            try:
                got = cache.make(conn, digest, search.EMBEDDING, source, recipe)
                done += 1 if got else 0
                failed += 0 if got else 1
            except Exception as error:  # keep going; the row records the reason
                failed += 1
                say(f"  {type(error).__name__}: {error}"[:160])
            if (done + failed) and (done + failed) % 250 == 0:
                rate = done / max(time.perf_counter() - started, 1e-9)
                left = work.owing(conn, search.EMBEDDING, recipe=recipe) - len(unreachable)
                say(f"{done:,} embedded ({from_archive:,} needed the archive)  {failed} failed"
                    f"  {len(unreachable):,} unreachable  {rate:.2f} img/s"
                    f"  ~{max(left, 0) / max(rate, 1e-9) / 3600:.1f} h left")

    say(f"finished: {done:,} embedded, {from_archive:,} from the archive,"
        f" {failed} failed, {len(unreachable):,} unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
