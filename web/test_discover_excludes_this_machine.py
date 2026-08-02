"""The computer you are sitting at is not somewhere to connect to.

Every Azimuth install advertises itself on the network, so an unfiltered browse
returns your own machine alongside the real hub. Measured on the owner's
laptop: two results, one of them itself, with nothing to tell them apart but a
hostname — and picking the wrong one pairs a library to itself.
"""

import unittest

SELF = "c5b9f5f7-f197-4074-82e8-eae6981f2354"
HUB = "8b5e09ca-1f88-451b-91ce-7a5f84d8be1a"


def offerable(hubs: list[dict], self_id: str) -> list[dict]:
    """The rule under test, in the same shape as the discover route."""

    if not self_id:
        return hubs
    return [hub for hub in hubs if hub.get("hub_id") != self_id]


class DiscoveryExcludesSelfTests(unittest.TestCase):
    def setUp(self):
        self.found = [
            {"name": "SeansXPS15", "url": "http://192.168.1.68:59282", "hub_id": SELF},
            {"name": "omarchy", "url": "http://192.168.1.72:8000", "hub_id": HUB},
        ]

    def test_this_machine_is_not_offered(self):
        names = [hub["name"] for hub in offerable(self.found, SELF)]
        self.assertEqual(names, ["omarchy"])

    def test_other_hubs_are_untouched(self):
        self.assertEqual(len(offerable(self.found, "someone-else")), 2)

    def test_an_unknown_identity_hides_nothing(self):
        """Better to show a hub you cannot use than to hide the only one there is."""

        self.assertEqual(len(offerable(self.found, "")), 2)

    def test_a_hub_advertising_no_identity_survives(self):
        anonymous = [{"name": "nas", "url": "http://nas.local:8000", "hub_id": ""}]
        self.assertEqual(offerable(anonymous, SELF), anonymous)


if __name__ == "__main__":
    unittest.main()
