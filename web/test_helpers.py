import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from core import responses as response_helpers  # noqa: E402
from core import work_coordination  # noqa: E402


class ImageHelperTests(unittest.TestCase):
    def image(self, image_id, **overrides):
        base = {
            "id": image_id,
            "filename": f"image-{image_id}.jpg",
            "filepath": f"/archive/set/image-{image_id}.jpg",
            "elo": 1200.0,
            "comparisons": 0,
            "propagated_updates": 0,
            "stars": 0,
            "status": "kept",
            "flag": "unflagged",
            "orientation": "landscape",
            "aspect_ratio": 1.5,
            "date_taken": "2024-05-10 12:00:00",
            "camera_make": "Fuji",
            "camera_model": "X-T5",
            "lens": "35mm",
            "file_ext": ".jpg",
            "file_size": 1234,
            "file_modified_at": 1700000000.0,
            "width": 3000,
            "height": 2000,
            "latitude": 41.0,
            "longitude": -87.0,
            "created_at": "2024-05-11 00:00:00",
        }
        base.update(overrides)
        return base

    def test_image_card_defaults_and_thumb_url(self):
        card = helpers.image_card({"id": 7, "filename": "seven.jpg"}, "md")

        self.assertEqual(card["id"], 7)
        self.assertEqual(card["filename"], "seven.jpg")
        self.assertEqual(card["elo"], 1200.0)
        self.assertEqual(card["comparisons"], 0)
        self.assertEqual(card["propagated_updates"], 0)
        self.assertEqual(card["status"], "kept")
        self.assertEqual(card["flag"], "unflagged")
        self.assertEqual(card["aspect_ratio"], 1.5)
        self.assertEqual(card["thumb_url"], "/api/thumb/md/7")
        self.assertIn("created_at", card)
        self.assertNotIn("similarity", card)
        self.assertNotIn("date_group", card)

    def test_image_card_contextual_similarity_and_date_group(self):
        card = helpers.image_card(
            self.image(3, elo=1321.25),
            "sm",
            similarity=0.987654,
            date_group="2024-05",
        )

        self.assertEqual(card["elo"], 1321.2)
        self.assertEqual(card["similarity"], 0.9877)
        self.assertEqual(card["date_group"], "2024-05")

    def test_image_card_helper_facade_matches_core_response_helper(self):
        image = self.image(5, elo=1333.36, comparisons=8, propagated_updates=2)

        self.assertIs(helpers.image_card, response_helpers.image_card)
        self.assertIs(helpers.metadata_payload, response_helpers.metadata_payload)
        self.assertIs(helpers.METADATA_FIELDS, response_helpers.METADATA_FIELDS)
        self.assertEqual(
            helpers.image_card(
                image,
                "md",
                similarity=0.812345,
                date_group="2024-05",
            ),
            response_helpers.image_card(
                image,
                "md",
                similarity=0.812345,
                date_group="2024-05",
            ),
        )
        self.assertEqual(
            helpers.metadata_payload(image),
            response_helpers.metadata_payload(image),
        )

    def test_ranking_signal_checks_direct_propagated_and_imported(self):
        self.assertFalse(helpers.has_ranking_signal(self.image(1)))
        self.assertTrue(helpers.has_ranking_signal(self.image(2, comparisons=1)))
        self.assertTrue(helpers.has_ranking_signal(self.image(3, propagated_updates=1)))
        self.assertTrue(helpers.has_ranking_signal(self.image(4, elo=1200.02)))

    def test_filter_dimensions(self):
        images = [
            self.image(1, orientation="landscape", comparisons=0, elo=1200.0, flag="unflagged"),
            self.image(2, orientation="portrait", comparisons=2, elo=1400.0, stars=4, flag="picked"),
            self.image(3, orientation="landscape", comparisons=10, elo=1510.0, stars=5, flag="rejected"),
        ]

        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, orientation="portrait")],
            [2],
        )
        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, compared="uncompared")],
            [1],
        )
        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, compared="confident")],
            [3],
        )
        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, min_stars=4)],
            [2, 3],
        )
        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, flag="rejected")],
            [3],
        )

    def test_filter_composition_and_metadata(self):
        images = [
            self.image(1, filepath="/archive/trips/a.jpg", camera_make="Fuji", camera_model="X-T5", lens="35mm"),
            self.image(2, filepath="/archive/home/b.jpg", camera_make="Fuji", camera_model="X-T5", lens="50mm"),
            self.image(3, filepath="/archive/trips/c.raw", camera_make="Canon", camera_model="R5", lens="35mm", file_ext=".raw"),
            self.image(4, filepath="/archive/trips/d.jpg", date_taken=None, camera_make="Fuji", camera_model="X-T5", lens="35mm"),
        ]

        filtered = helpers.filter_compare_mosaic_candidates(
            images,
            folder="trips",
            date_taken="2024",
            file_type="jpg",
            camera="Fuji X-T5",
            lens="35mm",
        )

        self.assertEqual([img["id"] for img in filtered], [1])
        self.assertEqual(
            [img["id"] for img in helpers.filter_compare_mosaic_candidates(images, date_taken="undated")],
            [4],
        )

    def test_empty_filter_is_noop_copy(self):
        images = [self.image(1), self.image(2)]
        filtered = helpers.filter_compare_mosaic_candidates(images)

        self.assertEqual([img["id"] for img in filtered], [1, 2])
        self.assertIsNot(filtered[0], images[0])

    def test_min_stars_filters_on_stored_projection_not_elo(self):
        # Stars are the stored Elo projection; a high raw Elo without a stored
        # star must not pass the filter.
        filtered = helpers.filter_compare_mosaic_candidates(
            [self.image(1, elo=1900.0, stars=0), self.image(2, elo=1301.0, stars=4)],
            min_stars=4,
        )
        self.assertEqual([img["id"] for img in filtered], [2])

    def test_db_backed_helpers_use_configured_providers(self):
        old_cached = helpers._cached_image_ids_provider
        calls = []

        async def fake_cached_image_ids(image_ids, size, cache_root):
            calls.append(("cached", tuple(image_ids), size, cache_root))
            return {2, 3}

        try:
            helpers.configure(cached_image_ids=fake_cached_image_ids)
            cached = asyncio.run(helpers.cached_image_ids([1, "2", "bad", 2, 3], "sm", "/tmp/cache"))
        finally:
            helpers._cached_image_ids_provider = old_cached

        self.assertEqual(cached, {2, 3})
        self.assertIn(("cached", (1, 2, 3), "sm", "/tmp/cache"), calls)


class WorkCoordinationTests(unittest.TestCase):
    def tearDown(self):
        for kind in list(work_coordination.status()["manual_active"]):
            work_coordination.finish_manual_bulk(kind)

    def test_manual_bulk_marks_ambient_warming_as_waiting(self):
        self.assertFalse(work_coordination.manual_bulk_active())
        self.assertTrue(work_coordination.ambient_warming_allowed())

        with work_coordination.manual_bulk("cache"):
            status = work_coordination.status()

            self.assertTrue(work_coordination.manual_bulk_active())
            self.assertFalse(work_coordination.ambient_warming_allowed())
            self.assertEqual(status["lanes"]["manual_bulk"]["state"], "running")
            self.assertEqual(status["lanes"]["ambient_warming"]["state"], "waiting")
            self.assertEqual(status["manual_active"], ["cache"])

        self.assertFalse(work_coordination.manual_bulk_active())

    def test_user_visible_lane_never_waits_for_manual_bulk(self):
        async def run():
            with work_coordination.manual_bulk("embeddings"):
                await work_coordination.wait_for_lane(work_coordination.USER_VISIBLE)
                return work_coordination.status()

        status = asyncio.run(run())

        self.assertEqual(status["lanes"]["user_visible"]["state"], "ready")
        self.assertEqual(status["lanes"]["manual_bulk"]["active"], ["embeddings"])


if __name__ == "__main__":
    unittest.main()
