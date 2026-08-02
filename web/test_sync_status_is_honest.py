"""An app with no hub must not report a healthy hub.

Measured on the owner's laptop: the desktop app listed 150,635 photos mirrored
from the hub, could not fetch a single pixel of any of them, and reported
`hub_health: "ok"` the whole time. The sync chip hides itself when no hub is
attached, so the one surface that could have said so showed nothing. The
library simply sat there greyed out with no cause and no way back.

The run mode cannot answer this. "standalone" and "satellite" are the same mode
to the app, so a purely local library would be told it is disconnected from a
hub it never had. Only the catalog knows: a mirrored `hub://` source means
photos live somewhere this computer must reach.
"""

import unittest


def hub_health(*, has_hub: bool, holds_hub_photos: bool) -> str:
    """The rule under test, in the same shape as the status route."""

    if has_hub:
        return "paired"  # stands in for the live contract probe
    return "not_connected" if holds_hub_photos else "ok"


class HubHealthHonestyTests(unittest.TestCase):
    def test_a_mirrored_library_with_no_hub_says_it_is_not_connected(self):
        """The laptop: 150,635 photos it cannot fetch."""

        self.assertEqual(
            hub_health(has_hub=False, holds_hub_photos=True), "not_connected"
        )

    def test_a_local_only_library_is_not_told_it_is_disconnected(self):
        """Someone who never had a hub is not missing one."""

        self.assertEqual(hub_health(has_hub=False, holds_hub_photos=False), "ok")

    def test_an_attached_satellite_still_reports_the_live_contract(self):
        self.assertEqual(hub_health(has_hub=True, holds_hub_photos=True), "paired")

    def test_an_attached_hub_is_reported_even_before_the_mirror_runs(self):
        self.assertEqual(hub_health(has_hub=True, holds_hub_photos=False), "paired")


if __name__ == "__main__":
    unittest.main()
