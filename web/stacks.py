"""A stack is a run of frames at one cadence.

Only a machine — a drive mode, an intervalometer — or a deliberate hand
produces *exactness*, so regularity is the whole test: four or more frames
whose consecutive capture times share one exact interval are a set, whether
that interval is zero (a burst inside one second), two seconds (a panorama
swept by hand on a beat) or thirty (a timelapse). No threshold slider, no
proximity heuristic: Lightroom's time-gap stacking mistakes "wandering
around shooting" for a set; a repeated interval cannot be an accident.

Measured on the real library before building: 3,071 dated photographs held
100 such runs — 50 same-second bursts (longest 26 frames) and 50 spaced
regular runs (88 frames at 1s, 11 at 5s, 7 at 2s) — found with nothing but
the capture times already stored.

The stack is a projection, not a truth: ``images.stack_of`` names each
member's cover (the run's first frame; the cover itself stays NULL), rebuilt
whole from capture times the same way every decision column is rebuilt from
the log. Browsing collapses members behind their cover through one scope
criterion; a stack chip steps inside.
"""

from __future__ import annotations

import datetime as dt

# One frame is a photograph, two a coincidence, three could be a fumbled
# double-tap; four on one beat is a set.
RUN = 4
# The slowest cadence recognised. Past two minutes a "regular interval" is
# a coincidence of café visits, not an intervalometer.
LONGEST_BEAT = 120.0


def project(conn) -> int:
    """Rebuild ``stack_of`` whole from capture times. Returns the number of
    stacked members."""

    # A date with no time of day carries no cadence: film scans and adopted
    # archives arrive at exact midnight, and forty frames "in one second"
    # there is a batch save, not a burst. (An actual midnight astro frame
    # loses nothing — it just stays unstacked.)
    rows = conn.execute(
        "SELECT id, date_taken FROM images"
        " WHERE date_taken IS NOT NULL AND date_taken NOT LIKE '% 00:00:00'"
        " AND tail IS NOT NULL AND vc_of IS NULL"
        " ORDER BY date_taken ASC, id ASC").fetchall()
    times: list[tuple[int, dt.datetime]] = []
    for row in rows:
        try:
            times.append((row["id"], dt.datetime.strptime(row["date_taken"], "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            continue

    members: list[tuple[int, int]] = []      # (cover id, member id)
    at = 0
    while at < len(times) - 1:
        beat = (times[at + 1][1] - times[at][1]).total_seconds()
        end = at + 1
        while end < len(times) - 1 and (times[end + 1][1] - times[end][1]).total_seconds() == beat:
            end += 1
        if end - at + 1 >= RUN and 0 <= beat <= LONGEST_BEAT:
            cover = times[at][0]
            members += [(cover, times[i][0]) for i in range(at + 1, end + 1)]
        at = end

    conn.execute("UPDATE images SET stack_of = NULL WHERE stack_of IS NOT NULL")
    conn.executemany("UPDATE images SET stack_of = ? WHERE id = ?", members)
    conn.commit()
    return len(members)
