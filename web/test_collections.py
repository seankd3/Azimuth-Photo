from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.collections import suggestions as collection_suggestions
from features.collections import routes as collection_routes


class CollectionTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        collection_routes.configure(
            create_collection=lambda **kwargs: db.create_collection(**kwargs),
            list_collections=lambda: db.list_collections(),
            get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
            rename_collection=lambda collection_id, **kwargs: db.rename_collection(collection_id, **kwargs),
            delete_collection=lambda collection_id: db.delete_collection(collection_id),
            add_collection_images=lambda collection_id, image_ids: db.add_collection_images(collection_id, image_ids),
            remove_collection_images=lambda collection_id, image_ids: db.remove_collection_images(
                collection_id,
                image_ids,
            ),
            get_suggestions=lambda: collection_suggestions.collection_suggestions(
                db.DB_PATH,
                db_signature=db.DB_PATH,
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

    async def test_collection_api_renames_collection(self):
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name="Original")
        )

        renamed = await collection_routes.api_rename_collection(
            created["collection"]["id"],
            collection_routes.RenameCollectionBody(name="  Final selects  "),
        )

        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["collection"]["name"], "Final selects")

    async def test_collection_api_rename_rejects_unknown_and_bad_names(self):
        missing = await collection_routes.api_rename_collection(
            999999,
            collection_routes.RenameCollectionBody(name="Missing"),
        )
        blank = await collection_routes.api_rename_collection(
            999999,
            collection_routes.RenameCollectionBody(name="   "),
        )
        too_long = await collection_routes.api_rename_collection(
            999999,
            collection_routes.RenameCollectionBody(name="x" * 161),
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(blank.status_code, 400)
        self.assertEqual(too_long.status_code, 400)

    async def test_collection_api_deletes_collection_without_deleting_photos(self):
        source = await self._source()
        image_id = await self._image(source["id"], "kept.jpg")
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name="Delete me", image_ids=[image_id])
        )

        deleted = await collection_routes.api_delete_collection(created["collection"]["id"])
        detail = await collection_routes.api_collection(created["collection"]["id"])
        row = await self._image_row(image_id)

        self.assertEqual(deleted, {"ok": True})
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(row["id"], image_id)

    async def test_collection_api_delete_returns_not_found_for_missing_collection(self):
        response = await collection_routes.api_delete_collection(999999)

        self.assertEqual(response.status_code, 404)

    async def test_collection_http_rename_delete_lifecycle(self):
        source = await self._source()
        image_id = await self._image(source["id"], "member.jpg")
        collection = await db.create_collection(name="Original", image_ids=[image_id])

        def probe():
            client = TestClient(app_module.app)
            try:
                renamed = client.post(
                    f"/api/user-collections/{collection['id']}/rename",
                    json={"name": "  Trimmed name  "},
                )
                after_rename = client.get(f"/api/user-collections/{collection['id']}")
                invalid = client.post(
                    f"/api/user-collections/{collection['id']}/rename",
                    json={"name": "   "},
                )
                deleted = client.post(f"/api/user-collections/{collection['id']}/delete")
                missing = client.get(f"/api/user-collections/{collection['id']}")
                return renamed, after_rename, invalid, deleted, missing
            finally:
                client.close()

        renamed, after_rename, invalid, deleted, missing = await asyncio.to_thread(probe)

        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["collection"]["name"], "Trimmed name")
        self.assertEqual(after_rename.status_code, 200)
        self.assertEqual(after_rename.json()["collection"]["name"], "Trimmed name")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"ok": True})
        self.assertEqual(missing.status_code, 404)

    async def test_collection_api_returns_not_found_for_missing_collection(self):
        response = await collection_routes.api_collection(999999)

        self.assertEqual(response.status_code, 404)

    async def test_collection_suggestions_route_returns_suggestions_shape(self):
        source = await self._source()
        first = await self._image(source["id"], "event-first.jpg")
        second = await self._image(source["id"], "event-second.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                [
                    ("2025-01-10 09:00:00", first),
                    ("2025-01-10 09:30:00", second),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()

        response = await collection_routes.api_collection_suggestions()

        self.assertEqual(response, {"suggestions": []})


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

    def test_shoot_hint_uses_starbase_filename_date(self):
        from features.collections.suggestions import shoot_hint_from_path

        hint = shoot_hint_from_path(
            "/run/media/sean/Expansion/Photos/Exported Edits/2024/All Selected/"
            "SKD-Starbase-2024-04-24-N01758.jpg"
        )

        self.assertEqual(hint["title"], "Starbase")
        self.assertEqual(hint["key"], "starbase-2024-04-24")
        self.assertEqual(hint["source"], "filename")
        self.assertIsNotNone(hint["date"])

    def test_shoot_hint_uses_human_folder_when_filename_is_counter(self):
        from features.collections.suggestions import shoot_hint_from_path

        hint = shoot_hint_from_path(
            "/run/media/sean/Expansion/Photos/Exported Edits/2022/Events/"
            "New York Air Show/quick edits/NYS Airshow-16.jpg"
        )

        self.assertEqual(hint["title"], "New York Air Show")
        self.assertEqual(hint["key"], "new-york-air-show")
        self.assertEqual(hint["source"], "folder")

    def test_shoot_hint_cleans_people_and_wedding_prefixes(self):
        from features.collections.suggestions import shoot_hint_from_path

        lauren = shoot_hint_from_path(
            "/run/media/sean/Expansion/Photos/Exported Edits/2022/Portraits/"
            "Lauren Elphin/Lauren_Elphin-0025.jpg"
        )
        wedding = shoot_hint_from_path(
            "/run/media/sean/Expansion/Photos/Exported Edits/2020/2020-07-18/"
            "Kathryn&JoesephWedding205of684.jpg"
        )

        self.assertEqual(lauren["title"], "Lauren Elphin")
        self.assertEqual(wedding["title"], "Kathryn & Joeseph Wedding")

    def test_build_shoot_candidates_titles_date_pattern(self):
        from features.collections.suggestions import _build_shoot_candidates

        rows = [
            {
                "id": i,
                "filename": f"SKD-Starbase-2024-04-24-N{i:05d}.jpg",
                "filepath": (
                    "/run/media/sean/Expansion/Photos/Exported Edits/2024/All Selected/"
                    f"SKD-Starbase-2024-04-24-N{i:05d}.jpg"
                ),
                "date_taken": f"2024-04-24 12:{i:02d}:00",
                "elo": 1200 + i,
                "camera_model": "EOS R5",
            }
            for i in range(1, 10)
        ]

        shoots, fallback = _build_shoot_candidates(rows)

        self.assertEqual(fallback, [])
        self.assertEqual(len(shoots), 1)
        self.assertEqual(shoots[0]["title"], "Starbase - Apr 24, 2024")
        self.assertEqual(shoots[0]["reason"], "Same filename pattern")
        self.assertEqual(shoots[0]["count"], 9)
