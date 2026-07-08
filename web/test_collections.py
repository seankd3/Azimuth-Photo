from test_support import *  # noqa: F401,F403
from features.collections import routes as collection_routes


class CollectionTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        collection_routes.configure(
            create_collection=lambda **kwargs: db.create_collection(**kwargs),
            list_collections=lambda: db.list_collections(),
            get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
            add_collection_images=lambda collection_id, image_ids: db.add_collection_images(collection_id, image_ids),
            remove_collection_images=lambda collection_id, image_ids: db.remove_collection_images(
                collection_id,
                image_ids,
            ),
        )

    async def test_collection_api_creates_reads_and_updates_membership(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")

        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Japan selects",
                description="Photos worth editing and sharing.",
                image_ids=[first, second, first, 999999],
            )
        )

        collection = created["collection"]
        self.assertTrue(created["ok"])
        self.assertEqual(collection["name"], "Japan selects")
        self.assertEqual(collection["description"], "Photos worth editing and sharing.")
        self.assertEqual(collection["visibility"], "private")
        self.assertEqual(collection["status"], "draft")
        self.assertEqual(collection["image_count"], 2)
        self.assertEqual(collection["cover_image_id"], first)

        listed = await collection_routes.api_user_collections()
        self.assertEqual([item["id"] for item in listed["collections"]], [collection["id"]])

        detail = await collection_routes.api_collection(collection["id"])
        self.assertEqual([image["id"] for image in detail["collection"]["images"]], [first, second])

        updated = await collection_routes.api_remove_collection_images_post(
            collection["id"],
            collection_routes.CollectionImagesBody(image_ids=[first]),
        )
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["collection"]["image_count"], 1)
        self.assertEqual(updated["collection"]["cover_image_id"], second)

    async def test_collection_api_returns_not_found_for_missing_collection(self):
        response = await collection_routes.api_collection(999999)

        self.assertEqual(response.status_code, 404)


class SuggestionGroupingTests(unittest.TestCase):
    def _row(self, image_id, taken, elo=1200.0, camera=""):
        return {"id": image_id, "taken": taken, "elo": elo, "camera_model": camera}

    def test_group_events_splits_on_gap_and_drops_small_groups(self):
        from features.collections.suggestions import group_events

        hour = 3600.0
        first = [self._row(i, i * hour * 0.5) for i in range(1, 13)]
        # 7-hour gap starts a new group that is too small to keep.
        small = [self._row(100, first[-1]["taken"] + 7 * hour)]
        second_start = small[0]["taken"] + 8 * hour
        second = [
            self._row(200 + i, second_start + i * hour * 0.25, elo=1500.0 + i)
            for i in range(12)
        ]

        events = group_events(first + small + second, gap_seconds=6 * hour, min_photos=12)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["image_ids"], [row["id"] for row in first])
        self.assertEqual(events[1]["image_ids"], [row["id"] for row in second])
        # Cover is the highest-elo member.
        self.assertEqual(events[1]["cover_image_id"], second[-1]["id"])

    def test_group_events_reports_dominant_camera(self):
        from features.collections.suggestions import group_events

        rows = [
            self._row(i, i * 60.0, camera="EOS R5" if i % 3 else "X100V")
            for i in range(1, 16)
        ]
        events = group_events(rows, gap_seconds=3600.0, min_photos=12)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["camera"], "EOS R5")
