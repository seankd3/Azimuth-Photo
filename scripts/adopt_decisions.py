"""Collect the owner's judgements out of the old schema and into the log.

Measured before writing this: a 2.4 GB catalog of 84 tables holds about 89,378
rows the owner actually decided. Everything else is recomputable. So this reads
the eight places a judgement was kept and appends each one to `decisions`,
after which the log — not any column — is the thing that must never be lost.

**It only ever appends.** No old table is dropped, no column is cleared, no row
is updated. A repeat run adopts nothing, because a decision already in the log
is recognised by subject, family, instant *and value* together. That is what
makes it safe to run against the live catalog with no ceremony: the worst case
is that it did nothing.

**Subjects are content hashes wherever one exists**, because a hash survives
the rows being rebuilt, the ids being renumbered and the file being moved to
another drive — which is the entire reason the log is keyed on identity rather
than on `image_id`. A photo with no hash yet is filed under `image:<id>` and
gets re-filed when it is identified.

What is deliberately *not* adopted: `images.elo` and `images.comparisons`. Not
because they are worthless — the opposite. Most of those Elos were **propagated
through the embedding space**, which is the mechanism that lets 2,532
comparisons order 157,000 photographs. But a propagated score is a derivation
of *comparisons plus vectors*, and both inputs keep growing: every new
comparison and every embedding off the owed queue should re-rank everything
that resembles it. Freezing the output as if it were judgement is exactly what
stopped it improving. The judgement is the 2,532 pairs; the ranking is computed
from them, again and again.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

from model import decisions  # noqa: E402


def _epoch(value) -> float | None:
    """A stored timestamp as an epoch, or None when it is not a time.

    The catalog spells times three ways depending on which year the writing
    code was from. A decision whose time cannot be read is still adopted — it
    just lands at the moment of adoption, which is the honest answer.
    """

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(str(value)[:19], shape))
        except ValueError:
            continue
    return None


def _subjects(conn) -> dict[int, str]:
    """image id -> the identity its decisions are filed under."""

    return {
        int(row["id"]): row["content_hash"] or f"image:{row['id']}"
        for row in conn.execute("SELECT id, content_hash FROM images")
    }


def _already(conn) -> tuple[set, set]:
    """What the log already holds, in the two shapes a repeat run must match.

    Two shapes, because the old schema recorded *when* for only some
    judgements, and each mistake here cost a run to find:

    * **Timed sources** — comparisons, edits, trashings — dedupe on the exact
      instant *and* the value. Keyed on `(subject, family, at)` alone, 2,532
      comparison pairs adopted as 564: timestamps have one-second resolution
      and a photo can win several comparisons inside one second, so every pair
      after the first collapsed into its neighbour.
    * **Timeless sources** — stars, flags, keywords, quality — dedupe on the
      value alone, because there is no recorded instant and inventing `now`
      made every rerun look new. That is how a second run added 6,360
      duplicates.

    A star the owner set, imported twice, is one decision. Two comparisons of
    the same pair at different times are two.
    """

    timed, valued = set(), set()
    for row in conn.execute("SELECT subject, family, at, value FROM decisions"):
        timed.add((row["subject"], row["family"], round(float(row["at"]), 3), row["value"]))
        valued.add((row["subject"], row["family"], row["value"]))
    return timed, valued


def adopt(conn, *, dry_run: bool = False) -> dict[str, int]:
    subject_of = _subjects(conn)
    timed, valued = _already(conn)
    now = time.time()
    tally: dict[str, int] = {}

    def keep(image_id, family, value, at=None):
        subject = subject_of.get(int(image_id))
        if subject is None or value is None:
            return
        payload = json.dumps(value)
        when = _epoch(at)
        if when is None:
            # The source never recorded an instant. Dedupe on what was decided.
            if (subject, family, payload) in valued:
                return
            when = now
        elif (subject, family, round(when, 3), payload) in timed:
            return
        timed.add((subject, family, round(when, 3), payload))
        valued.add((subject, family, payload))
        tally[family] = tally.get(family, 0) + 1
        if not dry_run:
            decisions.decide(conn, subject, family, value, at=when)

    def table_exists(name) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    # Stars and flags: the two judgements the owner makes fastest, and the ones
    # no derivation ever wrote to.
    for row in conn.execute("SELECT id, stars, flag, status, trashed_at FROM images"):
        if row["stars"]:
            keep(row["id"], decisions.STAR, int(row["stars"]))
        if row["flag"] and row["flag"] != "unflagged":
            keep(row["id"], "flag", row["flag"])
        if row["status"] and row["status"] != "kept":
            keep(row["id"], decisions.STATUS, row["status"], row["trashed_at"])

    # The pair ledger. This is the ranking judgement in full: which of these two
    # photographs is better, asked and answered 2,532 times. Elo is a reading of
    # this, and is computed rather than adopted.
    if table_exists("comparisons"):
        for row in conn.execute("SELECT winner_id, loser_id, mode, created_at FROM comparisons"):
            winner, loser = subject_of.get(int(row["winner_id"])), subject_of.get(int(row["loser_id"]))
            if winner and loser:
                keep(row["winner_id"], decisions.COMPARE,
                     {"beat": loser, "mode": row["mode"]}, row["created_at"])

    # Edits. Stored per image and per virtual copy; the settings blob is the
    # decision, and its history is the log's own ordering from here on.
    if table_exists("develop_settings"):
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(develop_settings)")}
        stamp = "updated_at" if "updated_at" in columns else "NULL"
        payload = "settings_json" if "settings_json" in columns else "settings"
        if payload in columns:
            for row in conn.execute(f"SELECT image_id, {payload} AS v, {stamp} AS at FROM develop_settings"):
                keep(row["image_id"], decisions.DEVELOP, row["v"], row["at"])

    if table_exists("image_keywords") and table_exists("keywords"):
        for row in conn.execute(
            "SELECT ik.image_id, k.name FROM image_keywords ik JOIN keywords k ON k.id = ik.keyword_id"
        ):
            keep(row["image_id"], "keyword", row["name"])

    if table_exists("image_quality"):
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(image_quality)")}
        if "image_id" in columns:
            field = "score" if "score" in columns else next(iter(columns - {"image_id"}), None)
            if field:
                for row in conn.execute(f"SELECT image_id, {field} AS v FROM image_quality"):
                    keep(row["image_id"], "quality", row["v"])

    if not dry_run:
        conn.commit()
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog")
    parser.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = parser.parse_args()

    conn = sqlite3.connect(args.catalog)
    conn.row_factory = sqlite3.Row
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "web", "model", "schema.sql"), encoding="utf-8") as handle:
        conn.executescript(handle.read())

    tally = adopt(conn, dry_run=not args.apply)
    total = sum(tally.values())
    for family in sorted(tally):
        print(f"  {family:10} {tally[family]:>7,}")
    print(f"  {'total':10} {total:>7,}{'' if args.apply else '   (dry run; pass --apply)'}")
    print(f"  log now holds {conn.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
