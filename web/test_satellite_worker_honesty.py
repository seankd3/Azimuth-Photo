"""Background work a device never does must say so, not spin.

On a hub-backed satellite the hub owns faces and captions; the laptop only
displays the results. Both panels used to report `status_stale`, which the
drawer renders as "Refreshing…" — describing work that was never going to
start. AI already said "unavailable" honestly; these now match it.

The interesting case is a machine where the dependency packs *are* installed
(so the old "not installed" answer does not apply) but the hub does the work.
That is Sean's laptop.
"""

import asyncio
import unittest
from unittest import mock

from features.captions import routes as caption_routes
from features.people import routes as people_routes

INSTALLED = {"available": True, "message": "People recognition dependencies are installed."}


class SatelliteWorkerHonestyTests(unittest.TestCase):
    def setUp(self):
        # The payload refuses to run unconfigured; wire the two providers it
        # insists on so the test exercises the reporting branch, not the setup.
        caption_routes.configure(
            get_caption_status_counts=mock.AsyncMock(return_value={}),
            invalidate_settings_response_cache=mock.Mock(),
        )
        people_routes.configure(
            get_people_review=mock.AsyncMock(return_value={"counts": {}}),
            get_people_status_counts=mock.AsyncMock(return_value={"people": 0}),
            get_face_thumbnail_context=mock.AsyncMock(return_value=None),
            label_person=mock.AsyncMock(return_value={}),
            merge_people=mock.AsyncMock(return_value={}),
            reject_merge_suggestion=mock.AsyncMock(return_value={}),
            assign_face=mock.AsyncMock(return_value={}),
            ignore_face=mock.AsyncMock(return_value={}),
            ignore_person=mock.AsyncMock(return_value={}),
        )

    def _people(self, *, deferred: bool, capability=INSTALLED) -> dict:
        with mock.patch.object(people_routes.satellite, "defers_bulk_compute", return_value=deferred), \
             mock.patch.object(people_routes.capabilities, "capability_status", return_value=capability):
            return asyncio.run(people_routes.people_status_payload())

    def _captions(self, *, deferred: bool, capability=INSTALLED) -> dict:
        with mock.patch.object(caption_routes.satellite, "defers_bulk_compute", return_value=deferred), \
             mock.patch.object(caption_routes.capabilities, "capability_status", return_value=capability):
            return asyncio.run(caption_routes.caption_status_payload())

    def test_faces_say_unavailable_on_a_hub_backed_satellite(self):
        payload = self._people(deferred=True)
        self.assertEqual(payload["worker"]["state"], "unavailable")
        self.assertFalse(payload["status_stale"], "never-starting work must not read as refreshing")
        self.assertIn("hub", payload["worker"]["message"].lower())

    def test_captions_say_unavailable_on_a_hub_backed_satellite(self):
        payload = self._captions(deferred=True)
        self.assertEqual(payload["worker"]["state"], "unavailable")
        self.assertFalse(payload["status_stale"])
        self.assertIn("hub", payload["worker"]["message"].lower())

    def test_a_missing_dependency_pack_still_says_so(self):
        absent = {"available": False, "message": "People recognition is not installed."}
        payload = self._people(deferred=True, capability=absent)
        self.assertEqual(payload["worker"]["state"], "unavailable")
        self.assertIn("not installed", payload["worker"]["message"].lower(),
                      "a missing pack is a different problem and keeps its own message")

    def test_the_payload_says_unavailable_so_the_drawer_can_prefer_it(self):
        """What the drawer needs to tell the difference.

        Measured on the laptop: worker.state was already "unavailable" and
        correct, but the drawer checked status_stale first and so said
        "Refreshing…" about work with no worker to do it. status_stale itself is
        honest — the counts really are being refreshed — so the fix is in the
        drawer, and this only guarantees the state it reads is there.
        """

        absent = {"available": False, "message": "People recognition is not installed."}
        for payload in (
            self._people(deferred=False, capability=absent),
            self._captions(deferred=False, capability=absent),
        ):
            self.assertEqual(payload["worker"]["state"], "unavailable")

    def test_a_machine_that_does_its_own_work_keeps_its_real_status(self):
        payload = self._captions(deferred=False)
        self.assertNotIn("hub", payload["worker"].get("message", "").lower(),
                         "a machine doing its own captioning is not deferring to anything")
        self.assertNotEqual(payload["worker"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
