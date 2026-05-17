import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
import resource_governor  # noqa: E402
from core import responses as response_helpers  # noqa: E402


class ImageHelperTests(unittest.TestCase):
    def image(self, image_id, **overrides):
        base = {
            "id": image_id,
            "filename": f"image-{image_id}.jpg",
            "filepath": f"/archive/set/image-{image_id}.jpg",
            "elo": 1200.0,
            "comparisons": 0,
            "propagated_updates": 0,
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
            self.image(2, orientation="portrait", comparisons=2, elo=1400.0, flag="picked"),
            self.image(3, orientation="landscape", comparisons=10, elo=1510.0, flag="rejected"),
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

    def test_db_backed_helpers_use_configured_providers(self):
        old_cached = helpers._cached_image_ids_provider
        old_active = helpers._get_active_images_by_ids_provider
        old_thresholds = helpers._star_thresholds
        calls = []

        async def fake_cached_image_ids(image_ids, size, cache_root):
            calls.append(("cached", tuple(image_ids), size, cache_root))
            return {2, 3}

        async def fake_active_images_by_ids(image_ids):
            calls.append(("active", tuple(image_ids)))
            return {int(image_id): self.image(int(image_id)) for image_id in image_ids}

        try:
            helpers.configure(
                cached_image_ids=fake_cached_image_ids,
                get_active_images_by_ids=fake_active_images_by_ids,
                star_thresholds={4: 1300},
            )
            cached = asyncio.run(helpers.cached_image_ids([1, "2", "bad", 2, 3], "sm", "/tmp/cache"))
            visible = asyncio.run(helpers.visible_ranked_images([1, 2, 3], 1, "sm", "/tmp/cache"))
            visible_count = asyncio.run(helpers.count_visible_ranked_ids([1, 2, 3], "sm", "/tmp/cache"))
            filtered = helpers.filter_compare_mosaic_candidates(
                [self.image(1, elo=1299), self.image(2, elo=1301)],
                min_stars=4,
            )
        finally:
            helpers._cached_image_ids_provider = old_cached
            helpers._get_active_images_by_ids_provider = old_active
            helpers._star_thresholds = old_thresholds

        self.assertEqual(cached, {2, 3})
        self.assertEqual([img["id"] for img in visible], [2])
        self.assertEqual(visible_count, 2)
        self.assertEqual([img["id"] for img in filtered], [2])
        self.assertIn(("cached", (1, 2, 3), "sm", "/tmp/cache"), calls)
        self.assertIn(("active", (2, 3)), calls)


class ResourceGovernorTests(unittest.TestCase):
    def setUp(self):
        self.old_read_load_1m = resource_governor._read_load_1m
        self.old_read_meminfo = resource_governor._read_meminfo
        self.old_cpu_count = resource_governor.os.cpu_count

    def tearDown(self):
        resource_governor._read_load_1m = self.old_read_load_1m
        resource_governor._read_meminfo = self.old_read_meminfo
        resource_governor.os.cpu_count = self.old_cpu_count

    def test_system_busy_uses_small_thumbnail_batches(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 8.2
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=120, work_mode="balanced")

        self.assertEqual(decision.reason, "system busy")
        self.assertEqual(decision.work_mode, "balanced")
        self.assertEqual(decision.thumbnail_batch_size, 1)
        self.assertGreaterEqual(decision.thumbnail_pause_seconds, 3.0)

    def test_light_mode_ignores_recent_activity_with_small_batches(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=30, work_mode="balanced")

        self.assertEqual(decision.reason, "light background")
        self.assertFalse(decision.pause)
        self.assertEqual(decision.thumbnail_batch_size, 2)
        self.assertGreaterEqual(decision.thumbnail_pause_seconds, 1.5)
        self.assertTrue(decision.can_start_heavy_work)

    def test_healthy_system_uses_bounded_thumbnail_batches(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=120, work_mode="balanced")

        self.assertEqual(decision.reason, "light background")
        self.assertEqual(decision.mode, "light")
        self.assertEqual(decision.thumbnail_batch_size, 2)
        self.assertFalse(decision.pause)

    def test_browse_mode_pauses_background_compute(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=999, work_mode="browse")

        self.assertEqual(decision.reason, "browse mode")
        self.assertTrue(decision.pause)
        self.assertFalse(decision.can_start_heavy_work)
        self.assertEqual(decision.thumbnail_batch_size, 0)

    def test_max_mode_uses_larger_batches_when_healthy(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=20, work_mode="max")

        self.assertEqual(decision.work_mode, "max")
        self.assertEqual(decision.reason, "system healthy")
        self.assertFalse(decision.pause)
        self.assertTrue(decision.can_start_heavy_work)
        self.assertGreater(decision.thumbnail_batch_size, 8)


if __name__ == "__main__":
    unittest.main()
