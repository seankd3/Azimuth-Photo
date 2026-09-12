"""A panorama sweep: consecutive frames that overlap one way, and its preview.

The fact first: that a run of frames was swept, not burst, so that S can
stack them as one and the inspector can say so. Then, asked for, the
preview: the frames merged from their loupe tiles (OpenCV's stitcher:
bundle adjustment, wave correction, gain compensation, seams and a
multi-band blend), the black canvas trimmed, kept as one file on the run.
No photograph is written into the library by this module; the full-size
merge that becomes a stack's cover is PN2. The rule (docs/panorama-research.md):

* **the frames** -- three to MOST, consecutive by capture time, each within
  SWEEP_GAP seconds of the last, one camera, one size, one folder;
* **the overlap** -- on the 1,024 px tiles, ORB features matched between
  neighbours and verified by a RANSAC homography: each pair's overlap in
  [OVERLAP_LEAST, OVERLAP_MOST] (a burst overlaps almost wholly and is
  refused at its first pair) and its inliers past Brown & Lowe's bar
  (5.9 + 0.22 x matches); the frame two steps on overlaps less than the
  next one; and the sweep keeps one direction.

The judge looks at one pair at a time and stops at the first refusal, so a
burst costs two frames' features and one match (a fifth of a second), and
the whole run is looked at only when it is a sweep. The verdict is kept on
the run's first frame, keyed by the run's members, so a frame rejected or
brought in changes the run and the answer with it.

Measured on the owner's catalog (445 runs of three or more frames within
five seconds): one sweep accepted, 444 bursts and repeats refused. False
positives are the whole risk of a detector nobody else ships, so every gate
is a refusal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os

from model import cache
from stacks import _timed

SWEEP_GAP = 5.0        # seconds between consecutive frames of one sweep
LEAST = 3              # frames
MOST = 36              # frames: three rows of twelve; a longer run is a burst or a timelapse
OVERLAP_LEAST = 0.15
OVERLAP_MOST = 0.65
WANDER = 20.0          # degrees of circular spread the direction may keep
KEY = "orb1"           # the measure's version


def run_around(conn, photo_id: int) -> list[dict]:
    """The frames that could be a sweep with this one: consecutive by
    capture time within SWEEP_GAP of each other, one camera, one size, one
    folder. The frame alone when none are."""

    row = conn.execute(
        "SELECT id, content_hash AS hash, date_taken, camera_model, width, height, tail, develop"
        " FROM images WHERE id = ?", (int(photo_id),)).fetchone()
    when = _timed(row["date_taken"]) if row else None
    if when is None or not row["hash"]:
        return [dict(row)] if row else []
    reach = dt.timedelta(seconds=SWEEP_GAP * 40)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, content_hash AS hash, date_taken, camera_model, width, height, tail, develop FROM images"
        " WHERE tail IS NOT NULL AND vc_of IS NULL AND status != 'trashed' AND content_hash IS NOT NULL"
        " AND date_taken BETWEEN ? AND ? ORDER BY date_taken ASC, id ASC",
        ((when - reach).strftime("%Y-%m-%d %H:%M:%S"), (when + reach).strftime("%Y-%m-%d %H:%M:%S")),
    )]
    at = next((i for i, r in enumerate(rows) if r["id"] == int(photo_id)), None)
    if at is None:
        return [dict(row)]

    def joined(a, b) -> bool:
        ta, tb = _timed(a["date_taken"]), _timed(b["date_taken"])
        return (ta is not None and tb is not None and (tb - ta).total_seconds() <= SWEEP_GAP
                and a["camera_model"] == b["camera_model"] and (a["width"], a["height"]) == (b["width"], b["height"])
                and a["tail"].rsplit("/", 1)[0] == b["tail"].rsplit("/", 1)[0])

    first = at
    while first > 0 and joined(rows[first - 1], rows[first]):
        first -= 1
    last = at
    while last + 1 < len(rows) and joined(rows[last], rows[last + 1]):
        last += 1
    return rows[first:last + 1]


def _features(path: str):
    import cv2

    grey = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if grey is None:
        return None
    keypoints, descriptors = cv2.ORB_create(2000).detectAndCompute(grey, None)
    if descriptors is None or len(keypoints) < 30:
        return None
    return grey.shape, keypoints, descriptors


def pair(a, b) -> dict | None:
    """How the second frame sits against the first: the overlap fraction,
    the direction it moved (degrees), how far (as a share of the width),
    and whether the match is verified. None when no homography holds."""

    import cv2
    import numpy as np

    if a is None or b is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(a[2], b[2], k=2)
    good = [m for m, n in (mn for mn in matches if len(mn) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None
    src = np.float32([a[1][m.queryIdx].pt for m in good])
    dst = np.float32([b[1][m.trainIdx].pt for m in good])
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if homography is None:
        return None
    inliers = int(mask.sum())
    h, w = a[0]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    inside = np.clip(warped, [0, 0], [w, h]).astype(np.float32)
    overlap = float(cv2.contourArea(inside)) / float(w * h)
    # The camera's own motion: the scene slides the other way in the frame.
    centre = cv2.perspectiveTransform(np.float32([[[w / 2, h / 2]]]), homography).reshape(2)
    dx, dy = float(w / 2 - centre[0]), float(h / 2 - centre[1])
    return {
        "overlap": round(overlap, 3),
        "angle": math.degrees(math.atan2(dy, dx)),
        "step": math.hypot(dx, dy) / w,
        "verified": inliers > 5.9 + 0.22 * len(good),
    }


def judge(frames: list[dict], tile_of) -> dict | None:
    """The verdict on a run: the sweep as {members, overlap, direction} or
    None, with `tile_of(frame)` naming each frame's 1,024 px tile."""

    import numpy as np

    if not LEAST <= len(frames) <= MOST:
        return None
    # One pair at a time, each gate as soon as it can be asked: a burst is
    # refused at its first pair, before the rest of the run is read.
    features = [_features(tile_of(frames[0]))]
    pairs = []
    for at in range(1, len(frames)):
        features.append(_features(tile_of(frames[at])))
        near = pair(features[at - 1], features[at])
        if near is None or not near["verified"] or not OVERLAP_LEAST <= near["overlap"] <= OVERLAP_MOST:
            return None
        if at >= 2:
            far = pair(features[at - 2], features[at])
            if far is not None and far["overlap"] >= pairs[-1]["overlap"]:
                return None
        pairs.append(near)
    overlaps = [p["overlap"] for p in pairs]
    angles = np.radians([p["angle"] for p in pairs])
    spread = abs(np.mean(np.exp(1j * angles)))
    if math.degrees(math.sqrt(-2 * math.log(max(spread, 1e-9)))) > WANDER:
        return None
    steps = [p["step"] for p in pairs]
    if np.std(steps) / max(float(np.mean(steps)), 1e-9) > 0.5:
        return None
    heading = math.degrees(math.atan2(float(np.mean(np.sin(angles))), float(np.mean(np.cos(angles)))))
    direction = ("left to right" if -45 <= heading < 45 else "top to bottom" if 45 <= heading < 135
                 else "right to left" if heading >= 135 or heading < -135 else "bottom to top")
    return {"members": [int(f["id"]) for f in frames], "overlap": round(float(np.mean(overlaps)), 2),
            "direction": direction}


KIND = cache.Kind(name="sweep", compute=lambda source, hash: None, params=("model", "run"), evictable=True)
MERGE = cache.Kind(name="merge", compute=lambda source, hash: None, params=("model", "run"), evictable=True,
                   remove=lambda path: os.path.isfile(path) and os.unlink(path))
MERGE_KEY = "stitch1"   # the merge's version


def _tile_of(tiles, size: int):
    """The frame's tile at this size: the plain one, or an edited frame's
    own rendition when that is the one on disk (its tiles are keyed by the
    edit)."""

    def tile(frame) -> str:
        plain = tiles.path(frame["hash"], size)
        if os.path.isfile(plain) or not frame.get("develop"):
            return plain
        return tiles.path(frame["hash"], size, json.loads(frame["develop"]))

    return tile


def of(conn, tiles, photo_id: int) -> dict | None:
    """The sweep this photograph is in, or None: judged once per run and
    kept as a cache row on the run's first frame keyed by the run's members,
    so the inspector's ask costs a read after the first, and a run that
    changes is judged again. Nothing is kept until every tile is there to
    look at: a run the library has not drawn yet is not a refused one. The
    answer carries the run's key (`first`, `run`) for the preview."""

    import render

    frames = run_around(conn, photo_id)
    if not LEAST <= len(frames) <= MOST:
        return None
    first = frames[0]["hash"]
    run = hashlib.sha1("".join(f["hash"] for f in frames).encode()).hexdigest()[:16]
    recipe = {"model": KEY, "run": run}
    held = cache.get(conn, first, KIND, recipe)
    if held is not None and held.get("state") == cache.READY:
        verdict = json.loads(held["value"]) if held.get("value") else None
    else:
        tile_of = _tile_of(tiles, render.GRID)
        if not all(os.path.isfile(tile_of(frame)) for frame in frames):
            return None
        verdict = judge(frames, tile_of)
        if held is None:
            made = json.dumps(verdict, separators=(",", ":")) if verdict else ""
            cache.put(conn, first, KIND, cache.Made(value=made, bytes=len(made)), recipe)
            conn.commit()
    if not verdict or int(photo_id) not in verdict["members"]:
        return None
    return {**verdict, "first": first, "run": run}


def _trimmed(pano):
    """The merged canvas without its black border: whichever edge is
    blackest is trimmed until none is more than a hundredth black, judged
    on an eighth-scale mask so a 9,000 px canvas costs milliseconds."""

    import cv2
    import numpy as np

    scale = 8
    small = cv2.resize(pano, (max(1, pano.shape[1] // scale), max(1, pano.shape[0] // scale)), interpolation=cv2.INTER_AREA)
    mask = (small.max(axis=2) > 8).astype(np.float32)
    top, bottom, left, right = 0, mask.shape[0], 0, mask.shape[1]
    while bottom - top > 2 and right - left > 2:
        edges = (("top", 1 - mask[top, left:right].mean()), ("bottom", 1 - mask[bottom - 1, left:right].mean()),
                 ("left", 1 - mask[top:bottom, left].mean()), ("right", 1 - mask[top:bottom, right - 1].mean()))
        side, black = max(edges, key=lambda e: e[1])
        if black <= 0.01:
            break
        top, bottom, left, right = (top + (side == "top"), bottom - (side == "bottom"),
                                    left + (side == "left"), right - (side == "right"))
    return pano[top * scale:bottom * scale, left * scale:right * scale]


def merge(paths: list[str]):
    """The frames at these paths merged into one canvas (BGR), trimmed, or
    None when the stitcher cannot place them."""

    import cv2

    frames = [cv2.imread(path) for path in paths]
    if any(frame is None for frame in frames):
        return None
    status, pano = cv2.Stitcher.create(cv2.Stitcher_PANORAMA).stitch(frames)
    if status != cv2.Stitcher_OK or pano is None:
        return None
    return _trimmed(pano)


def _merge_recipe(sweep: dict) -> dict:
    return {"model": MERGE_KEY, "run": sweep["run"]}


def previewed(conn, sweep: dict) -> dict | None:
    """The kept preview of this sweep -- path, width, height -- or None."""

    held = cache.get(conn, sweep["first"], MERGE, _merge_recipe(sweep))
    if held is None or held.get("state") != cache.READY or not held.get("path") or not os.path.isfile(held["path"]):
        return None
    return {"path": held["path"], **json.loads(held["value"])}


def preview(conn, tiles, sweep: dict) -> dict | None:
    """The sweep merged from its loupe tiles (the grid tiles where a loupe
    is not there yet), kept as one file beside the tiles on the run's first
    frame. Seconds of work: the caller runs it off the window's lane. None,
    remembered, when the stitcher cannot place the frames."""

    import cv2
    import render

    held = previewed(conn, sweep)
    if held is not None:
        return held
    recipe = _merge_recipe(sweep)
    if (row := cache.get(conn, sweep["first"], MERGE, recipe)) is not None and row.get("state") == cache.FAILED:
        return None
    frames = [dict(r) for r in conn.execute(
        f"SELECT id, content_hash AS hash, develop FROM images WHERE id IN ({','.join('?' * len(sweep['members']))})"
        " ORDER BY date_taken ASC, id ASC", sweep["members"])]
    loupe_of, grid_of = _tile_of(tiles, render.LOUPE), _tile_of(tiles, render.GRID)
    paths = [loupe_of(f) if os.path.isfile(loupe_of(f)) else grid_of(f) for f in frames]
    pano = merge(paths) if all(os.path.isfile(p) for p in paths) else None
    if pano is None:
        cache.failed(conn, sweep["first"], MERGE, "the stitcher could not place the frames", recipe)
        conn.commit()
        return None
    path = os.path.join(tiles.root, sweep["first"][:2], f"{sweep['first']}-merge-{sweep['run']}.jpg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, pano, [cv2.IMWRITE_JPEG_QUALITY, 88])
    size = {"width": int(pano.shape[1]), "height": int(pano.shape[0])}
    cache.put(conn, sweep["first"], MERGE, cache.Made(path=path, value=json.dumps(size), bytes=os.path.getsize(path)), recipe)
    conn.commit()
    return {"path": path, **size}
