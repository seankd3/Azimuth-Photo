"""Scenes and their leads: the first step of the editing workspace.

A scene is the unit the whole workspace turns on (`docs/SELECT.md`): a run
of frames of one subject, of which exactly one is shown. The cadence law
already finds the frames taken on one beat (`stacks.runs`); a scene is that
run widened by likeness, because a subject outlasts a burst -- twenty
frames of her on the summit, over eleven minutes, in two bursts, are one
scene and not two.

The widening needs a bar, and a bar is a knob unless the library sets it
itself. It does: the runs the cadence law already found are this library's
own sample of what one subject looks like in the embedding space, so their
internal likeness says how alike two frames must be to belong together
here. A library of tight portrait bursts sets a high bar; one of loose
landscape work sets a low one. Nobody types a number.

A scene's lead is the highest-scoring frame in it, by the fit that learns
from the person's rounds (`rank.fitted`). With no rounds yet the fit is
flat and the lead is the first frame; the person's swaps are what make it
mean anything, which is the whole design: the space says what a photograph
is like, never what it is worth.
"""

from __future__ import annotations

import stacks
from library import IN_LIBRARY
from model.scope import EVERYTHING, Scope

# Where the bar sits in the runs' own likeness. The tenth percentile, so a
# scene may be as loose as the loosest tenth of the bursts this library
# actually contains -- generous enough to carry a subject across a gap in
# shooting, mean enough to keep two subjects apart.
BAR_PERCENTILE = 10
# A run needs this many frames before its likeness is evidence about the
# library rather than about one pair.
TELLING = 3


def _frames(conn, scope: Scope) -> list[dict]:
    where = f"{IN_LIBRARY} AND i.vc_of IS NULL AND i.date_taken IS NOT NULL"
    if scope and scope.sql != "1":
        where = f"{where} AND ({scope.sql})"
    return [
        {"id": int(r["id"]), "hash": r["hash"], "when": r["date_taken"]}
        for r in conn.execute(
            "SELECT i.id, i.content_hash AS hash, i.date_taken FROM images i"
            f" WHERE {where} ORDER BY i.date_taken ASC, i.id ASC", scope.args)
    ]


def _beats(frames: list[dict]) -> list[list[int]]:
    """The scope cut into runs by the cadence law, singles between them."""

    times = [stacks._timed(f["when"]) for f in frames]
    keep = [i for i, t in enumerate(times) if t is not None]
    if not keep:
        return [[i] for i in range(len(frames))]
    runs = stacks.runs([times[i] for i in keep])
    spoken = set()
    groups: list[list[int]] = []
    for first, last in runs:
        members = [keep[i] for i in range(first, last + 1)]
        groups.append(members)
        spoken.update(members)
    loners = [i for i in range(len(frames)) if i not in spoken]
    groups.extend([i] for i in loners)
    groups.sort(key=lambda g: g[0])
    return groups


def _bar(groups: list[list[int]], placed: dict, matrix) -> float | None:
    """How alike two frames must be to be one scene here, read off the runs
    this library already has. None when it has too few to say."""

    import numpy as np

    said = []
    for group in groups:
        if len(group) < TELLING:
            continue
        rows = [placed[i] for i in group if i in placed]
        if len(rows) < TELLING:
            continue
        block = matrix[rows]
        said.extend(float(block[k] @ block[k + 1]) for k in range(len(rows) - 1))
    if len(said) < TELLING:
        return None
    return float(np.percentile(said, BAR_PERCENTILE))


def scenes(conn, scope: Scope = EVERYTHING, space=None) -> list[list[int]]:
    """The scope as scenes, in capture order, every photograph in exactly
    one. Without a space this is the cadence law alone."""

    frames = _frames(conn, scope)
    if not frames:
        return []
    groups = _beats(frames)

    placed: dict[int, int] = {}
    matrix = None
    if space is not None and space[1] is not None and len(space[0]):
        at = {subject: row for row, subject in enumerate(space[0])}
        placed = {i: at[f["hash"]] for i, f in enumerate(frames) if f["hash"] in at}
        matrix = space[1]

    bar = _bar(groups, placed, matrix) if placed else None
    if bar is not None:
        # Neighbours in time join when their facing frames are at least as
        # alike as this library's own bursts. One pass, forward: a subject
        # carries across as far as it keeps being the same subject.
        joined: list[list[int]] = [groups[0]]
        for group in groups[1:]:
            back, front = joined[-1][-1], group[0]
            if back in placed and front in placed and \
                    float(matrix[placed[back]] @ matrix[placed[front]]) >= bar:
                joined[-1] = joined[-1] + group
            else:
                joined.append(group)
        groups = joined

    return [[frames[i]["id"] for i in group] for group in groups]


def leads(conn, scope: Scope = EVERYTHING, space=None,
          scores: dict[str, float] | None = None) -> list[dict]:
    """Every scene with the frame that speaks for it. The lead is the
    highest-scoring member by the fit; with no rounds the fit is flat and
    the first frame speaks, which is honest -- nothing has been judged."""

    found = scenes(conn, scope, space)
    if not found:
        return []
    everyone = [pid for scene in found for pid in scene]
    marks = ",".join("?" * len(everyone))
    by_id = {
        int(r["id"]): r["hash"]
        for r in conn.execute(
            f"SELECT id, content_hash AS hash FROM images WHERE id IN ({marks})", everyone)
    }
    scores = scores or {}
    said = []
    for scene in found:
        best = max(scene, key=lambda pid: (scores.get(by_id.get(pid) or "", 0.0), -pid))
        said.append({"lead": best, "members": scene, "frames": len(scene)})
    return said
