"""Carry the owner's judgement from the V1 catalog into a V2 home.

**The map is (tail, file size) → identity, and everything else follows.**
V1 named photographs by a 32-hex partial hash; V2 identity is the full-byte
BLAKE2b of the file. Nothing can convert one into the other, but both
catalogs know each file's tail and byte size, so once V2 has identified a
file the two rows meet — and every judgement keyed on the old name can be
re-said under the new one. Ambiguous keys (two rows sharing tail and size)
are excluded and counted, never guessed.

What carries, and how:

* **Decisions** — star and rotate verbatim; V1's `flag` and `status` fold
  into V2's one status vocabulary (picked stays picked, rejected is
  trashed); every compare pair is re-said whole (`{"over": [...]}`) with
  both members mapped or not at all; develop rows carry untouched for the
  Develop wave to interpret. Timestamps are preserved, so history reads in
  order. Idempotent: a row whose (subject, family, at, value) already
  stands is not said twice.
* **Keywords** — V1 keyword rows become keyword-kind sets with their
  members, which is what the V2 search's named door already reads.
* **Embeddings** — the SigLIP vectors the backfill spent GPU-hours making
  are copied under their new keys, never recomputed. `INSERT OR IGNORE`,
  so a vector V2 already made wins.
* **Grid tiles** — seeded from the V1 1920-px previews, downscaled to the
  grid's own 1024 and encoded by the same `render.encode` the store uses.
  1920 clears the renderer's own honesty bar for a 1024 answer, so this is
  the same answer the worker would make, hours sooner. The loupe is not
  seeded: 1920 is below its bar, and a dishonest tile is worse than a slow
  one.

Afterwards the read indexes are rebuilt from the adopted log (`reindex`)
and the ranking is recomputed with the adopted vectors (`rerank`), so Best
and the stars are yours again on first paint.

The V1 catalog is opened read-only and never written. Safe to re-run: each
pass adopts whatever became mappable since the last one — run it again
after the archive drive is attached and swept.

    python scripts/adopt.py <v2-home> [--source <v1 db>] [--previews <dir>]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

V1_DB = r"C:\Azimuth Photo\data\catalog\azimuth.db"
V1_PREVIEWS = r"C:\Azimuth Photo\thumbs\md"

# V1 spelled one judgement two ways ("picked" and {"value": "picked"}), and
# split the cull between `flag` and `status`. V2 has one vocabulary.
STATUS_FROM_V1 = {"picked": "picked", "rejected": "trashed", "trashed": "trashed"}


def say(message: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')}  {message}", flush=True)


def plain(value) -> str | None:
    """The judgement inside however V1 spelled it."""

    try:
        held = json.loads(value)
    except (TypeError, ValueError):
        return None
    if isinstance(held, dict):
        held = held.get("value")
    return held if isinstance(held, (str, int)) else held


def build_map(v1, v2) -> tuple[dict[str, str], dict[int, str], int]:
    """32-hex → 64-hex and V1 id → 64-hex, by unique (tail, size) on both
    sides. Returns the two maps and how many keys were ambiguous."""

    def unique(conn, sql):
        rows = {}
        doubled = set()
        for row in conn.execute(sql):
            key = (row[0], int(row[1]))
            if key in rows:
                doubled.add(key)
            else:
                rows[key] = row
        return {k: v for k, v in rows.items() if k not in doubled}, doubled

    old, old_doubled = unique(v1, "SELECT tail, file_size, content_hash, id FROM images"
                                  " WHERE tail IS NOT NULL AND file_size IS NOT NULL")
    new, new_doubled = unique(v2, "SELECT tail, file_size, content_hash, id FROM images"
                                  " WHERE tail IS NOT NULL AND file_size IS NOT NULL"
                                  " AND content_hash IS NOT NULL")
    hash_map: dict[str, str] = {}
    id_map: dict[int, str] = {}
    for key, (_tail, _size, old_hash, old_id) in old.items():
        met = new.get(key)
        if met is None:
            continue
        identity = str(met[2])
        if old_hash:
            hash_map[str(old_hash)] = identity
        id_map[int(old_id)] = identity
    return hash_map, id_map, len(old_doubled) + len(new_doubled)


def already(v2, subject: str, family: str, at, value_text: str) -> bool:
    return v2.execute(
        "SELECT 1 FROM decisions WHERE subject = ? AND family = ? AND at = ? AND value = ? LIMIT 1",
        (subject, family, at, value_text),
    ).fetchone() is not None


def adopt_decisions(v1, v2, hash_map) -> dict[str, int]:
    from model import decisions

    tally = {"star": 0, "rotate": 0, "status": 0, "compare": 0, "develop": 0,
             "unmapped": 0, "skipped": 0, "stood": 0}
    rows = []

    for row in v1.execute("SELECT subject, family, value, at FROM decisions ORDER BY at ASC, id ASC"):
        subject = hash_map.get(str(row[0]))
        family, value, at = str(row[1]), row[2], row[3]

        if family in ("quality", "chores", "collection_meta", "keyword"):
            tally["skipped"] += 1          # keyword carries separately, as sets
            continue
        if subject is None:
            tally["unmapped"] += 1
            continue

        if family == "star":
            held = plain(value)
            if not (isinstance(held, int) and 0 <= held <= 5):
                tally["skipped"] += 1
                continue
            rows.append((subject, decisions.STAR, json.dumps(held), at))
            tally["star"] += 1
        elif family == "rotate":
            held = plain(value)
            if held not in (0, 90, 180, 270):
                tally["skipped"] += 1
                continue
            rows.append((subject, decisions.ROTATE, json.dumps(held), at))
            tally["rotate"] += 1
        elif family in ("flag", "status"):
            held = STATUS_FROM_V1.get(plain(value))
            if held is None:
                tally["skipped"] += 1
                continue
            rows.append((subject, decisions.STATUS, json.dumps(held), at))
            tally["status"] += 1
        elif family == "compare":
            try:
                held = json.loads(value)
            except (TypeError, ValueError):
                tally["skipped"] += 1
                continue
            over = held.get("over") or ([held["beat"]] if held.get("beat") else [])
            mapped = [hash_map[h] for h in over if h in hash_map and hash_map[h] != subject]
            if not mapped:
                tally["unmapped"] += 1     # a round needs both sides
                continue
            rows.append((subject, decisions.COMPARE, json.dumps({"over": sorted(mapped)}), at))
            tally["compare"] += 1
        elif family == "develop":
            rows.append((subject, "develop", value, at))
            tally["develop"] += 1
        else:
            tally["skipped"] += 1

    for subject, family, value_text, at in rows:
        if already(v2, subject, family, at, value_text):
            tally["stood"] += 1
            tally[{"star": "star", "rotate": "rotate", "status": "status",
                   "compare": "compare", "develop": "develop"}.get(family, "skipped")] -= 1
            continue
        v2.execute("INSERT INTO decisions(subject, family, value, at, by) VALUES (?, ?, ?, ?, 'you')",
                   (subject, family, value_text, at))
    v2.commit()
    return tally


def adopt_keywords(v1, v2, hash_map) -> dict[str, int]:
    from model import sets

    wanted: dict[str, set[str]] = {}
    for row in v1.execute("SELECT subject, value FROM decisions WHERE family = 'keyword'"):
        name = plain(row[1])
        subject = hash_map.get(str(row[0]))
        if not name or not isinstance(name, str) or subject is None:
            continue
        wanted.setdefault(name.strip(), set()).add(subject)

    made = joined = 0
    held = {entry["name"]: entry["id"] for entry in sets.all(v2, kind=sets.KEYWORD)}
    for name, members in sorted(wanted.items()):
        set_id = held.get(name)
        if set_id is None:
            set_id = sets.create(v2, name, kind=sets.KEYWORD)
            made += 1
        standing = set(sets.members(v2, set_id))
        fresh = sorted(members - standing)
        if fresh:
            sets.add(v2, set_id, fresh)
            joined += len(fresh)
    v2.commit()
    return {"keyword_sets": made, "keyword_members": joined}


def adopt_embeddings(v1, v2, hash_map) -> dict[str, int]:
    import embed

    copied = stood = unmapped = 0
    batch = []
    for row in v1.execute(
        "SELECT hash, value, bytes, at FROM cache"
        " WHERE kind = 'embedding' AND recipe = ? AND state = 'ready' AND value IS NOT NULL",
        (embed.RECIPE,),
    ):
        identity = hash_map.get(str(row[0]))
        if identity is None:
            unmapped += 1
            continue
        batch.append((identity, embed.RECIPE, row[1], row[2], row[3]))
    for identity, recipe, value, size, at in batch:
        done = v2.execute(
            "INSERT OR IGNORE INTO cache(hash, kind, recipe, state, value, bytes, at)"
            " VALUES (?, 'embedding', ?, 'ready', ?, ?, ?)",
            (identity, recipe, value, size, at),
        )
        if done.rowcount:
            copied += 1
        else:
            stood += 1
    v2.commit()
    return {"vectors_copied": copied, "vectors_stood": stood, "vectors_unmapped": unmapped}


def seed_tiles(v2, id_map, store, previews: str) -> dict[str, int]:
    import io

    import render
    from model import cache
    from PIL import Image

    seeded = stood = absent = 0
    for old_id, identity in id_map.items():
        if cache.get(v2, identity, store.grid) is not None:
            stood += 1
            continue
        source = os.path.join(previews, f"{old_id}.jpg")
        if not os.path.exists(source):
            absent += 1
            continue
        try:
            with Image.open(source) as preview:
                sized = render.fit(preview.convert("RGB"), render.GRID)
                body = render.encode(sized)
        except Exception:
            absent += 1
            continue
        target = store.path(identity, render.GRID)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(body)
        cache.put(v2, identity, store.grid, cache.Made(path=target, bytes=len(body)))
        seeded += 1
        if seeded % 2000 == 0:
            v2.commit()
            say(f"  tiles seeded {seeded:,}")
    v2.commit()
    return {"tiles_seeded": seeded, "tiles_stood": stood, "tiles_absent": absent}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the V2 home folder (holds catalog/ and previews/)")
    parser.add_argument("--source", default=V1_DB)
    parser.add_argument("--previews", default=V1_PREVIEWS)
    args = parser.parse_args()

    import home as homes
    import library as queries
    import model
    import rank
    import tiles

    catalog, previews = homes.paths(args.home)
    if not os.path.exists(catalog):
        raise SystemExit(f"no V2 catalog at {catalog} — open the app there (or attach) first")

    v1 = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    v1.row_factory = sqlite3.Row
    v2 = model.connect(catalog, timeout=60.0)
    store = tiles.Store(previews)
    say(f"adopting into {args.home} from {args.source}")

    hash_map, id_map, ambiguous = build_map(v1, v2)
    say(f"mapped {len(id_map):,} photographs by (tail, size); {ambiguous} ambiguous keys excluded")

    tally: dict[str, int] = {"ambiguous": ambiguous, "mapped": len(id_map)}
    tally.update(adopt_decisions(v1, v2, hash_map))
    say(f"decisions: {tally['star']} stars, {tally['compare']} rounds, {tally['status']} statuses, "
        f"{tally['rotate']} rotates, {tally['develop']} develop; {tally['stood']} stood, "
        f"{tally['unmapped']} unmapped, {tally['skipped']} skipped")
    tally.update(adopt_keywords(v1, v2, hash_map))
    say(f"keywords: {tally['keyword_sets']} sets, {tally['keyword_members']} members")
    tally.update(adopt_embeddings(v1, v2, hash_map))
    say(f"vectors: {tally['vectors_copied']:,} copied, {tally['vectors_stood']:,} stood, "
        f"{tally['vectors_unmapped']:,} await their files")
    tally.update(seed_tiles(v2, id_map, store, args.previews))
    say(f"tiles: {tally['tiles_seeded']:,} seeded from previews, {tally['tiles_stood']:,} stood, "
        f"{tally['tiles_absent']:,} had no preview")

    rebuilt = queries.reindex(v2)
    ranked = queries.rerank(v2, *rank.space(v2))
    say(f"reindexed {sum(rebuilt.values())} projections; reranked {ranked:,} photographs")

    v1.close()
    v2.close()
    say("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
