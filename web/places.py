"""Where a photograph was taken: the file says, or a track fills the gap.

The camera knows *when*, the phone knows *where*, and position is a pure
function of time — the classic tracklog model. A GPX file (from the phone,
a watch, any logger) is read once; every dated photograph inside the
track's span that does not already know its place gets one, interpolated
between the two nearest points, as a ``place`` decision. A phone photo's
own EXIF position always wins: the file said it, nothing needs deciding.

The one honest wrinkle is clocks: GPX speaks UTC, a camera speaks local
time with no zone. ``offset_seconds`` is the camera's offset from UTC,
defaulting to this machine's — the camera on the desk was almost always
set by the same hands that set the computer.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

from model import decisions

# How far from the nearest track point a photograph may sit and still take a
# place from it. Past ten minutes the logger was off or elsewhere.
NEAREST = 600.0

_GPX = "{http://www.topografix.com/GPX/1/1}"
_GPX0 = "{http://www.topografix.com/GPX/1/0}"


def read_track(path: str) -> list[tuple[float, float, float]]:
    """Every timed point in one GPX file: (utc timestamp, lat, lon)."""

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    points: list[tuple[float, float, float]] = []
    for ns in (_GPX, _GPX0):
        for pt in root.iter(f"{ns}trkpt"):
            when = pt.find(f"{ns}time")
            if when is None or not when.text:
                continue
            try:
                stamp = dt.datetime.fromisoformat(when.text.replace("Z", "+00:00"))
                points.append((stamp.timestamp(),
                               float(pt.attrib["lat"]), float(pt.attrib["lon"])))
            except (KeyError, ValueError):
                continue
    points.sort()
    return points


def _at(points: list[tuple[float, float, float]], moment: float) -> tuple[float, float] | None:
    """The track's position at one moment, linearly interpolated; None when
    the track was not being written near it."""

    from bisect import bisect_left

    at = bisect_left(points, (moment,))
    before = points[at - 1] if at > 0 else None
    after = points[at] if at < len(points) else None
    if before and after and after[0] > before[0]:
        share = (moment - before[0]) / (after[0] - before[0])
        if min(moment - before[0], after[0] - moment) <= NEAREST:
            return (round(before[1] + share * (after[1] - before[1]), 6),
                    round(before[2] + share * (after[2] - before[2]), 6))
        return None
    edge = before or after
    if edge and abs(moment - edge[0]) <= NEAREST:
        return (edge[1], edge[2])
    return None


def adopt_track(conn, path: str, *, offset_seconds: float | None = None) -> int:
    """Correlate one GPX file against the library. Returns how many
    photographs learned their place."""

    points = read_track(path)
    if not points:
        return 0
    if offset_seconds is None:
        offset_seconds = (dt.datetime.now().astimezone().utcoffset()
                          or dt.timedelta()).total_seconds()

    span = (points[0][0] - NEAREST, points[-1][0] + NEAREST)
    # A file that carries its own position needs nothing decided.
    carried = {row["hash"] for row in conn.execute(
        "SELECT hash FROM cache WHERE kind = 'metadata' AND state = 'ready'"
        " AND value LIKE '%\"lat\"%'")}
    placed = 0
    for row in conn.execute(
        "SELECT DISTINCT i.content_hash AS hash, i.date_taken FROM images i"
        " WHERE i.date_taken IS NOT NULL AND i.content_hash IS NOT NULL"
        " AND i.tail IS NOT NULL"):
        try:
            local = dt.datetime.strptime(row["date_taken"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        # The camera's wall time, read in its own zone, is the UTC moment.
        moment = local.replace(
            tzinfo=dt.timezone(dt.timedelta(seconds=offset_seconds))).timestamp()
        if not span[0] <= moment <= span[1]:
            continue
        where = _at(points, moment)
        if where is None:
            continue
        if row["hash"] in carried or of(conn, row["hash"]) is not None:
            continue          # the file, or an earlier track, already said
        decisions.decide(conn, row["hash"], decisions.PLACE,
                         {"lat": where[0], "lon": where[1]}, by=decisions.FILE)
        placed += 1
    if placed:
        conn.commit()
    return placed


def of(conn, digest: str) -> dict | None:
    """The photograph's place decision, when one was ever made."""

    said = decisions.latest(conn, str(digest), decisions.PLACE)
    return said if isinstance(said, dict) else None
