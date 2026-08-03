"""What this node is.

The server is the archive. The laptop is the terminal for working in it. A node
is one of three things, and every question anyone asks about the relationship is
answered from that one value:

    hub         holds the archive and serves it to others
    satellite   works in an archive that lives somewhere else
    standalone  holds its own archive and serves nobody

Pairing happens at runtime, so the role is resolved per call rather than
snapshotted at import. That is the whole reason this module exists: the two
previous `is_hub_mode()` copies read os.environ directly, and a laptop paired
from the UI stores its hub in settings, not the environment — so after pairing
the laptop kept advertising itself as a hub over mDNS and kept showing the
Remote-access panel. Asking `hub_url()` instead of the environment fixes both.
"""

from __future__ import annotations

import os

HUB = "hub"
SATELLITE = "satellite"
STANDALONE = "standalone"


def _hub_url() -> str:
    # Imported here, not at module scope, because the hub URL and device token
    # still live in features/sync/satellite.py. archive/ importing features/ is
    # backwards and the lazy import only hides it: the state belongs here, and
    # moving it is 47 call sites through the pairing path. Recorded in
    # docs/SIMPLIFY_LOG.md rather than left looking deliberate.
    from features.sync import satellite

    return satellite.hub_url()


def role() -> str:
    """A declared mode wins; otherwise a configured hub decides.

    Attaching a hub is what turns a standalone install into a satellite, so a
    stored hub outranks the standalone default. It does not outrank an explicit
    AZIMUTH_MODE=hub: a machine told it is the archive stays the archive even if
    a stale hub URL is lying around in its settings.
    """
    mode = os.environ.get("AZIMUTH_MODE", "").strip().lower()
    if mode == HUB:
        return HUB
    if mode == SATELLITE:
        return SATELLITE  # configured as one, not paired yet
    if _hub_url():
        return SATELLITE  # paired at runtime, or pointed at a hub by env
    if mode == STANDALONE:
        return STANDALONE
    return HUB


def serves_the_archive() -> bool:
    """Advertise over mDNS, accept satellites, show the Remote-access panel."""
    return role() == HUB


def works_in_someone_elses_archive() -> bool:
    """The bytes and the canonical catalog may live on another machine."""
    return role() != HUB


def has_hub() -> bool:
    """This node works in someone else's archive and knows where it is.

    Not merely "a hub URL exists": a machine declared AZIMUTH_MODE=hub with a
    stale URL in its settings has no hub, it *is* one.
    """
    return works_in_someone_elses_archive() and bool(_hub_url())


def defers_bulk_compute() -> bool:
    """Leave AI, faces, captions and preview pregen to the hub.

    Standalone holds the canonical library, so it arms everything a hub arms,
    budgeted for its own host. Only a node whose archive is elsewhere defers.
    """
    return role() == SATELLITE
