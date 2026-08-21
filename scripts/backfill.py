"""Embed the library. Resumable; safe to re-run; safe to kill at any moment.

Every vector is recorded in `cache` as it is made, so stopping this loses at
most one batch and starting it again picks up where it stopped. It reads the
catalog's own answers for where a rendition is and where an original is, and
knows nothing else about either -- a tile store, a work loop or a search module
can be rebuilt underneath it without this noticing.

    python scripts/backfill.py

Written to be left alone for a day. Run it detached from any session that may
end before it does.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
sys.path.insert(0, WEB)
os.environ.setdefault("AZIMUTH_DATA_DIR", r"C:\Azimuth Photo\data")
os.environ.setdefault("HF_HOME", r"D:\azimuth-bench\models")


# Two, measured, not guessed. This card has 4,096 MiB and the weights hold
# 2,237 of it, so the batch is the only free variable and it is a cliff rather
# than a curve: 1 gives 0.80 img/s, 2 gives 1.37, and 8 gives 0.10 because the
# activations no longer fit and Windows spills them to system RAM instead of
# raising -- the same failure that made Qwen 2B unusable. A batch that does not
# fit does not fail, it crawls.
BATCH = 2
PAGE = 2000

# Where V1 kept its previews, named by row id. A tile row's `path` is a hint
# like a copy row is -- checked when it matters -- and the hint can point at a
# file only some processes can see: a Claude-desktop shell is an MSIX package,
# so anything it wrote under %LOCALAPPDATA% landed in that package's private
# overlay, invisible to every other process on the machine. The same bytes are
# hardlinked here under their V1 names, which every process can read.
V1_THUMBS = r"C:\Azimuth Photo\thumbs"
V1_SIZES = ("sm", "md", "lg")

# What is owed: a photograph in the library with no answer -- ready *or failed*
# -- from the model in use. A failure is an answer; asking again every pass is
# how a worker never reaches the readable photographs behind one broken file.
#
# `rendition` is the smallest tile recorded for the photograph, because the
# model sees 384px either way and a 400px JPEG on this disk is 10x cheaper to
# read than a RAW on the archive drive: measured 6 img/s against 0.56.
OWED = """
    SELECT i.id, i.content_hash AS hash, i.tail, i.file_size,
           (SELECT t.path FROM cache t
             WHERE t.hash = i.content_hash AND t.kind = 'tile'
               AND t.state = 'ready' AND t.path IS NOT NULL
             ORDER BY t.bytes ASC LIMIT 1) AS rendition
    FROM images i
    LEFT JOIN cache e ON e.hash = i.content_hash AND e.kind = 'embedding'
                     AND e.recipe = ?
    WHERE i.content_hash IS NOT NULL AND i.tail IS NOT NULL AND i.vc_of IS NULL
      AND i.status != 'trashed' AND e.hash IS NULL
      AND i.content_hash > ?
    GROUP BY i.content_hash
    HAVING (rendition IS NOT NULL) = ?
    ORDER BY i.content_hash
    LIMIT ?
"""


def renditions(row):
    """Every place a small JPEG of this photograph might be, best first."""

    if row["rendition"]:
        yield row["rendition"]
    for size in V1_SIZES:
        yield os.path.join(V1_THUMBS, size, f"{row['id']}.jpg")


def say(message: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')}  {message}", flush=True)


def main() -> int:
    import embed
    from core.catalog_path import catalog_path
    from model import photos

    path = catalog_path()
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    say(f"catalog {path}")

    # The recipe names the model, spelled exactly as `model.cache.canonical`
    # spells it, so the rows this writes are the rows the product reads.
    recipe = embed.RECIPE
    say(f"model {embed.KEY}")

    done = failed = unreachable = 0
    started = time.perf_counter()

    # Renditions first, then originals. Each phase walks the owed photographs in
    # hash order behind a cursor: a photograph that gets an answer leaves the
    # query, and one that cannot be reached on this machine is simply walked
    # past rather than recorded as a failure -- "not here" is a fact about the
    # machine, and a later run with the drive plugged in must not read it as a
    # fact about the photograph.
    for from_renditions in (True, False):
        cursor = ""
        phase_done = 0
        while True:
            rows = conn.execute(OWED, (recipe, cursor, int(from_renditions), PAGE)).fetchall()
            if not rows:
                break
            cursor = rows[-1]["hash"]

            for start in range(0, len(rows), BATCH):
                group = rows[start:start + BATCH]
                sources, digests = [], []
                for row in group:
                    source = next((p for p in renditions(row) if os.path.isfile(p)), None)
                    if source is None and from_renditions:
                        continue  # phase two reads the original
                    if source is None:
                        source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
                    if not source:
                        unreachable += 1
                        continue
                    sources.append(source)
                    digests.append(row["hash"])
                if not sources:
                    continue

                try:
                    made = embed.vectors(sources)
                except Exception as error:  # noqa: BLE001 - log and carry on
                    failed += len(sources)
                    say(f"  batch {type(error).__name__}: {error}"[:160])
                    continue

                now = time.time()
                rows_out = []
                for digest, vector in zip(digests, made):
                    if vector is None:
                        failed += 1
                        rows_out.append((digest, recipe, "failed", None, 0,
                                         "would not decode", now))
                    else:
                        done += 1
                        phase_done += 1
                        rows_out.append((digest, recipe, "ready", vector.tobytes(),
                                         len(vector) * 4, None, now))
                conn.executemany(
                    "INSERT OR REPLACE INTO cache(hash, kind, recipe, state, value, bytes, note, at)"
                    " VALUES (?, 'embedding', ?, ?, ?, ?, ?, ?)", rows_out)
                conn.commit()

                if done and (done // BATCH) % 30 == 0:
                    rate = done / max(time.perf_counter() - started, 1e-9)
                    say(f"{done:,} embedded  {failed} failed  {unreachable:,} unreachable"
                        f"  {rate:.2f} img/s  {'renditions' if from_renditions else 'originals'}")

        say(f"{'renditions' if from_renditions else 'originals'} done: {phase_done:,} embedded"
            f" in {(time.perf_counter() - started) / 3600:.1f} h")

    say(f"finished: {done:,} embedded, {failed} failed, {unreachable:,} unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
