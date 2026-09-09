"""A stack is a decision: these frames sit behind that one.

The person makes a stack -- a burst, a timelapse, a panorama sweep, a
bracket -- and the library keeps it as one decision per member naming the
cover. Nothing is stacked for you: a machine that guesses at sets over-groups
a walk into a set and hides the frame you wanted. What the machine keeps is
the *proposal*: press S on one frame and the cadence law below says which
neighbours belong with it, and that becomes the stack.

The cadence law. Only a machine -- a drive mode, an intervalometer -- or a
deliberate hand produces a *beat*, so regularity is the test: four or more
frames whose consecutive capture times keep one interval, within the jitter
a camera adds (focus and exposure: two seconds, or a third of the beat,
whichever is more). Measured on the owner's working catalog (6,121 dated
photographs, 2026-09-08): the exact law found 285 runs, this one 367.

``images.stack_of`` names each member's cover, a projection of the stack
decisions rebuilt like every decision column. Browsing shows stacks open,
with a band around each; the person collapses them, one or all.
"""

from __future__ import annotations

import datetime as dt
import statistics

from model import decisions, projection

# One frame is a photograph, two a coincidence, three could be a fumbled
# double-tap; four on one beat is a set.
RUN = 4
# The slowest cadence recognised. Past two minutes a "regular interval" is
# a coincidence of café visits, not an intervalometer.
LONGEST_BEAT = 120.0
# The jitter a camera adds to its own beat: focus and exposure, in seconds
# and as a share of the interval.
JITTER_SECONDS = 2.0
JITTER_SHARE = 1.0 / 3.0


def keeps_beat(gap: float, beat: float) -> bool:
    """Does this gap belong to a run whose median gap is `beat`?"""

    return abs(gap - beat) <= max(JITTER_SECONDS, JITTER_SHARE * beat)


def runs(times: list[dt.datetime]) -> list[tuple[int, int]]:
    """Every run in capture order, as (first index, last index) pairs."""

    gaps = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    found: list[tuple[int, int]] = []
    at = 0
    while at < len(gaps):
        held = [gaps[at]]
        end = at
        while end + 1 < len(gaps) and keeps_beat(gaps[end + 1], statistics.median(held)):
            held.append(gaps[end + 1])
            end += 1
        if len(held) + 1 >= RUN and 0 <= statistics.median(held) <= LONGEST_BEAT:
            found.append((at, end + 1))
        at = end + 1
    return found


def project(conn) -> int:
    """Rebuild ``stack_of`` from the stack decisions. Returns the number of
    stacked members."""

    cover_of = {member: cover for member, cover in decisions.current(conn, decisions.STACK).items() if cover}
    ids_of: dict[str, list[int]] = {}
    intended: dict[int, tuple] = {}
    for row in conn.execute(
        "SELECT id, content_hash FROM images WHERE tail IS NOT NULL AND vc_of IS NULL ORDER BY id"):
        intended[row["id"]] = (None,)
        if row["content_hash"]:
            ids_of.setdefault(row["content_hash"], []).append(row["id"])
    members = 0
    for member, cover in cover_of.items():
        cover_ids = ids_of.get(cover)
        if not cover_ids:
            continue
        for member_id in ids_of.get(member, ()):
            intended[member_id] = (cover_ids[0],)
            members += 1
    projection.project(conn, "id", ("stack_of",), intended)
    return members


def _timed(when: str | None) -> dt.datetime | None:
    if not when or len(when) < 19 or when.endswith(" 00:00:00"):
        return None
    try:
        return dt.datetime.fromisoformat(when)
    except ValueError:
        return None


def around(conn, photo_id: int) -> list[int]:
    """The frames the cadence law puts with this one, in capture order --
    what S proposes on a lone frame. Just the frame itself when it keeps no
    beat with its neighbours."""

    row = conn.execute("SELECT date_taken FROM images WHERE id = ?", (int(photo_id),)).fetchone()
    when = _timed(row["date_taken"]) if row else None
    if when is None:
        return [int(photo_id)]
    reach = dt.timedelta(seconds=LONGEST_BEAT * 60)
    rows = conn.execute(
        "SELECT id, date_taken FROM images WHERE tail IS NOT NULL AND vc_of IS NULL"
        " AND status != 'trashed' AND date_taken BETWEEN ? AND ? ORDER BY date_taken ASC, id ASC",
        ((when - reach).strftime("%Y-%m-%d %H:%M:%S"), (when + reach).strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()
    ids, times = [], []
    for entry in rows:
        stamp = _timed(entry["date_taken"])
        if stamp is None:
            continue
        ids.append(int(entry["id"]))
        times.append(stamp)
    if int(photo_id) not in ids:
        return [int(photo_id)]
    at = ids.index(int(photo_id))
    for first, last in runs(times):
        if first <= at <= last:
            return ids[first:last + 1]
    return [int(photo_id)]


def stack(conn, photo_ids) -> dict:
    """Make one stack of these photographs -- one of them means the cadence
    run around it. The earliest frame is the cover; the rest sit behind it,
    one decision each. Returns the cover and the members."""

    wanted = [int(i) for i in photo_ids]
    if len(wanted) == 1:
        wanted = around(conn, wanted[0])
    marks = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT id, content_hash FROM images WHERE id IN ({marks}) AND content_hash IS NOT NULL"
        " ORDER BY date_taken ASC, id ASC", wanted).fetchall()
    if len(rows) < 2:
        return {"cover": None, "members": []}
    cover = rows[0]
    for member in rows[1:]:
        decisions.decide(conn, member["content_hash"], decisions.STACK, cover["content_hash"])
    # A cover stands on its own: whatever it sat behind before, it no longer does.
    decisions.decide(conn, cover["content_hash"], decisions.STACK, None)
    project(conn)
    conn.commit()
    return {"cover": int(cover["id"]), "members": [int(r["id"]) for r in rows[1:]]}


def unstack(conn, photo_ids) -> dict:
    """Dissolve the stacks these photographs are in -- a cover takes its
    whole set apart, a member steps out alone. Returns the ids freed."""

    wanted = [int(i) for i in photo_ids]
    marks = ",".join("?" for _ in wanted)
    freed: list[int] = []
    for row in conn.execute(
            f"SELECT id, content_hash, stack_of FROM images WHERE id IN ({marks}) AND content_hash IS NOT NULL", wanted):
        if row["stack_of"] is not None:
            decisions.decide(conn, row["content_hash"], decisions.STACK, None)
            freed.append(int(row["id"]))
        for member in conn.execute("SELECT id, content_hash FROM images WHERE stack_of = ?", (row["id"],)):
            decisions.decide(conn, member["content_hash"], decisions.STACK, None)
            freed.append(int(member["id"]))
    project(conn)
    conn.commit()
    return {"unstacked": sorted(set(freed))}
