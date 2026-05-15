import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
import resource_governor  # noqa: E402


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
        self.assertEqual(decision.thumbnail_batch_size, 4)
        self.assertGreaterEqual(decision.thumbnail_pause_seconds, 1.0)

    def test_recent_user_activity_keeps_background_batches_small(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=30, work_mode="balanced")

        self.assertEqual(decision.reason, "recent user activity")
        self.assertTrue(decision.pause)
        self.assertEqual(decision.thumbnail_batch_size, 0)
        self.assertGreaterEqual(decision.thumbnail_pause_seconds, 5.0)

    def test_healthy_system_uses_bounded_thumbnail_batches(self):
        resource_governor.os.cpu_count = lambda: 8
        resource_governor._read_load_1m = lambda: 1.0
        resource_governor._read_meminfo = lambda: {
            "MemAvailable": 10 * 1024 ** 3,
            "SwapTotal": 10 * 1024 ** 3,
            "SwapFree": 10 * 1024 ** 3,
        }

        decision = resource_governor.get_background_decision(idle_seconds=120, work_mode="balanced")

        self.assertEqual(decision.reason, "system healthy")
        self.assertEqual(decision.thumbnail_batch_size, 8)
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
