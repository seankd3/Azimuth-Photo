"""Clusters propose themselves. Every embedded photograph names its strongest
resemblance among the palette, spoken in the model's own space, and the terms
with enough members become proposals — live smart collections waiting to be
kept. Nothing here is a fact: an assignment is derived twice over (the vector
from the tile, the name from the vector), rewrites whole whenever the space
grows, and wears a tilde until calibration turns a label into a decision.

The palette's text vectors are made once per palette per machine and kept as
a small file beside the catalog's previews, so proposing costs a matmul and
never a model load. They are computed only when the model is already warm —
the rank lane must never pull 2.4 GB onto the card uninvited.
"""

from __future__ import annotations

import os

import embed
from model import palette

KIND = "alike"
STAMP = palette.stamp(embed.KEY)

_palette = None   # (stamp, matrix) once loaded or made


def _store(catalog_path) -> str:
    """Where the palette vectors live: the home's cache directory."""

    home = os.path.dirname(os.path.dirname(str(catalog_path)))
    return os.path.join(home, "cache", f"palette-{STAMP.split('#')[-1]}.npz")


def vectors(catalog_path):
    """The palette matrix, or None when this machine cannot say yet."""

    global _palette
    if _palette is not None and _palette[0] == STAMP:
        return _palette[1]
    import numpy as np

    path = _store(catalog_path)
    if os.path.isfile(path):
        held = np.load(path)
        if str(held.get("stamp")) == STAMP:
            _palette = (STAMP, held["matrix"].astype(np.float32))
            return _palette[1]
    if not embed.warm():
        return None
    made = np.stack([embed.text(prompt) for _, prompt in palette.PALETTE])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez(path, stamp=np.array(STAMP), matrix=made)
    _palette = (STAMP, made.astype(np.float32))
    return _palette[1]


def available(catalog_path) -> str | None:
    """The stamp when proposing is possible here, else None."""

    return STAMP if vectors(catalog_path) is not None else None


# Tags, not a partition: a photograph wears every name nearly as strong as
# its strongest, capped at its best few. The margin is interim by design —
# calibration (phase 3) replaces it with a per-label threshold earned from
# the owner's own answers.
MARGIN = 0.9
MOST = 4
# A group found in the space itself only earns a row when the palette has
# not already told its story through the tags of most of its members.
TOLD = 0.6


def _groups(matrix, floor: int):
    """The space's own structure: k-means blobs, as (member-index lists).

    Plain Lloyd over the unit vectors — cosine and euclidean agree there —
    seeded so the same space yields the same groups every pass.
    """

    import numpy as np

    n = len(matrix)
    k = max(4, min(40, n // 12))
    if n < 2 * k:
        return []
    rng = np.random.default_rng(7)
    centers = matrix[rng.choice(n, size=k, replace=False)].copy()
    assign = np.zeros(n, dtype=np.int64)
    for _ in range(20):
        assign = (matrix @ centers.T).argmax(axis=1)
        for c in range(k):
            mine = matrix[assign == c]
            if len(mine):
                centers[c] = mine.mean(axis=0)
                centers[c] /= np.linalg.norm(centers[c]) or 1.0
    return [np.flatnonzero(assign == c) for c in range(k)
            if floor <= (assign == c).sum()]


def recluster(conn, subjects, matrix, catalog_path) -> int:
    """Every embedded photograph's names, rewritten whole — the same
    wholesale rhythm `rerank` uses for elo and stars. One row per
    photograph, a JSON list: its strongest palette resemblances, plus the
    name of any group the space itself formed around it that the palette
    had not already told. Both kinds of name ride the same rows, so the
    shelf, the chips and Keep never learn the difference."""

    import json

    import numpy as np

    held = vectors(catalog_path)
    if held is None or not len(subjects):
        return 0
    scores = matrix @ held.T
    names = [name for name, _ in palette.PALETTE]
    order = scores.argsort(axis=1)
    worn = []
    for row in range(len(subjects)):
        best = float(scores[row, order[row, -1]])
        worn.append([
            names[int(pick)]
            for pick in order[row, ::-1][:MOST]
            if float(scores[row, int(pick)]) >= MARGIN * best
        ])

    floor = max(4, len(subjects) // 200)
    taken = set(names)
    for members in _groups(matrix, floor):
        centre = matrix[members].mean(axis=0)
        centre /= np.linalg.norm(centre) or 1.0
        voice = (centre @ held.T).argsort()[::-1]
        first, second = names[int(voice[0])], names[int(voice[1])]
        told = sum(1 for m in members if first in worn[m]) / len(members)
        if told >= TOLD:
            continue
        name = f"{first} · {second}"
        if name in taken:
            continue
        taken.add(name)
        for m in members:
            worn[int(m)].append(name)

    rows = [(subject, KIND, STAMP, json.dumps(mine)) for subject, mine in zip(subjects, worn)]
    conn.execute("DELETE FROM cache WHERE kind = ?", (KIND,))
    conn.executemany(
        "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
        " VALUES (?, ?, ?, 'ready', ?, unixepoch())",
        rows,
    )
    conn.commit()
    return len(rows)


def proposals(conn, limit: int = 12) -> list[dict]:
    """The clusters worth offering: terms with enough members, largest first,
    excluding any term a kept collection already wears."""

    from model import sets

    kept = set()
    for entry in sets.all(conn, kind=sets.COLLECTION):
        for chip in entry.get("criteria") or []:
            if chip.get("is") == "alike":
                kept.update(chip.get("values") or [])
    # Assignments are identity-keyed and outlive the photographs on purpose
    # (a re-imported file finds its names waiting); a proposal only ever
    # counts what is actually in the library today.
    import library as queries

    counted = conn.execute(
        "SELECT j.value AS term, COUNT(DISTINCT c.hash) AS photos"
        " FROM cache c, json_each(CAST(c.value AS TEXT)) j"
        " WHERE c.kind IN (?, 'people') AND c.state = 'ready'"
        "   AND c.hash IN (SELECT i.content_hash FROM images i"
        f"                 WHERE {queries.IN_LIBRARY})"
        " GROUP BY term ORDER BY photos DESC",
        (KIND,),
    ).fetchall()
    embedded = int(conn.execute(
        "SELECT COUNT(*) FROM cache WHERE kind = ? AND state = 'ready'"
        "   AND hash IN (SELECT i.content_hash FROM images i"
        f"                WHERE {queries.IN_LIBRARY})",
        (KIND,),
    ).fetchone()[0])
    # Worth a row when it is a real fraction of what is embedded — half a
    # percent — with a small absolute floor so a young library still speaks.
    floor = max(4, embedded // 200)
    out = []
    for row in counted:
        if row["term"] in kept or row["photos"] < floor:
            continue
        out.append({"term": row["term"], "count": row["photos"]})
        if len(out) >= limit:
            break
    return out
