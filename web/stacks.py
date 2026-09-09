"""A stack is a run of frames at one cadence.

Only a machine — a drive mode, an intervalometer — or a deliberate hand
produces a *beat*, so regularity is the whole test: four or more frames
whose consecutive capture times keep one interval are a set, whether that
interval is zero (a burst inside one second), two seconds (a panorama swept
by hand on a beat) or thirty (a timelapse). No threshold slider, no
proximity heuristic: Lightroom's time-gap stacking mistakes "wandering
around shooting" for a set; a repeated interval cannot be an accident.

The beat is kept with the jitter a camera adds to it. An intervalometer
fires on time, but the frame's timestamp is the start of an exposure that
may be 1/60 s or 20 s in aperture priority, and autofocus hunts for a
moment before the shutter — so consecutive gaps on a 33 s beat read 31,
38, 30, 34. A gap belongs to a run when it agrees with the run's median
within **two seconds, or a third of the beat, whichever is more**: enough
for exposure and focus, not enough to glue a stroll into a set.

Measured on the owner's working catalog (6,121 dated photographs,
2026-09-08) before the tolerance was chosen: the exact law found 285 runs;
this one finds 367, and the gaps inside them stray by 10–40% of the beat
where they stray at all. At half the beat the count keeps climbing and the
new runs read 14, 12, 8 — walking, not a machine.

The stack is a projection, not a truth: ``images.stack_of`` names each
member's cover (the run's first frame; the cover itself stays NULL), rebuilt
whole from capture times the same way every decision column is rebuilt from
the log. Browsing collapses members behind their cover through one scope
criterion; a stack chip steps inside.
"""

from __future__ import annotations

import datetime as dt
import statistics

from model import projection

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
    """Rebuild ``stack_of`` whole from capture times. Returns the number of
    stacked members."""

    # A date with no time of day carries no cadence: film scans and adopted
    # archives arrive at exact midnight, and forty frames "in one second"
    # there is a batch save, not a burst. (An actual midnight astro frame
    # loses nothing — it just stays unstacked.)
    ids: list[int] = []
    times: list[dt.datetime] = []
    intended: dict[int, tuple] = {}
    for row in conn.execute(
        "SELECT id, date_taken FROM images"
        " WHERE tail IS NOT NULL AND vc_of IS NULL ORDER BY date_taken ASC, id ASC"):
        intended[row["id"]] = (None,)
        when = row["date_taken"]
        if not when or len(when) < 19 or when.endswith(" 00:00:00"):
            continue
        try:
            times.append(dt.datetime.fromisoformat(when))
        except ValueError:
            continue
        ids.append(row["id"])
    members = 0
    for first, last in runs(times):
        for i in range(first + 1, last + 1):
            intended[ids[i]] = (ids[first],)
            members += 1
    projection.project(conn, "id", ("stack_of",), intended)
    return members
