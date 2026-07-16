from fastapi.testclient import TestClient
import json

from test_support import *  # noqa: F401,F403
from features.collections import smart as smart_collections
from features.collections import suggestions as collection_suggestions
from features.collections import routes as collection_routes
from features.share import routes as share_routes


class CollectionTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._configure_collection_routes()

    async def _resolve_library_constraints(self, q: str = "", *, people: str = "", deep: bool = False):
        return {
            "id_filter": None,
            "text_query": (q or "").strip(),
        }

    def _configure_collection_routes(self):
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
            collection_is_smart=lambda collection_id: db.collection_is_smart(collection_id),
            resolve_smart_detail=lambda query, **kwargs: smart_collections.resolve_detail(
                query,
                resolve_library_constraints=self._resolve_library_constraints,
                count_rankings=lambda **count_kwargs: db.count_rankings(**count_kwargs),
                get_rankings=lambda **ranking_kwargs: db.get_rankings(**ranking_kwargs),
                **kwargs,
            ),
            resolve_smart_summary=lambda query: smart_collections.resolve_summary(
                query,
                resolve_library_constraints=self._resolve_library_constraints,
                count_rankings=lambda **count_kwargs: db.count_rankings(**count_kwargs),
                get_rankings=lambda **ranking_kwargs: db.get_rankings(**ranking_kwargs),
                db_signature=lambda: db.DB_PATH,
            ),
            resolve_smart_image_ids=lambda query: smart_collections.resolve_image_ids(
                query,
                resolve_library_constraints=self._resolve_library_constraints,
                count_rankings=lambda **count_kwargs: db.count_rankings(**count_kwargs),
                get_rankings=lambda **ranking_kwargs: db.get_rankings(**ranking_kwargs),
            ),
            resolve_smart_materialized_image_ids=lambda query: smart_collections.resolve_materialized_image_ids(
                query,
                resolve_library_constraints=self._resolve_library_constraints,
                count_rankings=lambda **count_kwargs: db.count_rankings(**count_kwargs),
                get_rankings=lambda **ranking_kwargs: db.get_rankings(**ranking_kwargs),
            ),
            get_suggestions=lambda: collection_suggestions.collection_suggestions(
                db.DB_PATH,
                db_signature=db.DB_PATH,
            ),
            db_path=lambda: db.DB_PATH,
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

    async def test_regular_collection_mutations_emit_uuid_backed_oplog_entries(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", ("a" * 32, first))
            await conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", ("b" * 32, second))
            await conn.commit()
        finally:
            await conn.close()
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name="Summer", image_ids=[first])
        )
        collection_id = created["collection"]["id"]
        self.assertTrue(created["collection"]["uuid"])

        await collection_routes.api_rename_collection(
            collection_id, collection_routes.RenameCollectionBody(name="Summer selects")
        )
        await collection_routes.api_add_collection_images(
            collection_id, collection_routes.CollectionImagesBody(image_ids=[second])
        )
        await collection_routes.api_remove_collection_images_post(
            collection_id, collection_routes.CollectionImagesBody(image_ids=[first])
        )
        await collection_routes.api_delete_collection(collection_id)

        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT family, payload FROM oplog ORDER BY seq"
            )).fetchall()
        finally:
            await conn.close()
        entries = [(row["family"], json.loads(row["payload"])) for row in rows]
        self.assertEqual(
            [family for family, _ in entries],
            [
                "collection_meta",
                "collection_membership",
                "collection_meta",
                "collection_membership",
                "collection_membership",
                "collection_meta",
            ],
        )
        self.assertEqual(entries[-1][1]["deleted"], True)
        self.assertTrue(all(payload["collection_uuid"] == created["collection"]["uuid"] for _, payload in entries))

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

    async def test_smart_collection_resolves_live_query_and_materializes(self):
        source = await self._source()
        first = await self._image(source["id"], "picked-a.jpg", elo=1400)
        second = await self._image(source["id"], "picked-b.jpg", elo=1300)
        third = await self._image(source["id"], "later-picked.jpg", elo=1500)
        await db.set_image_flag(first, "picked")
        await db.set_image_flag(second, "picked")

        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Picked",
                query={"flag": "picked", "sort": "elo"},
            )
        )
        collection_id = created["collection"]["id"]
        detail = await collection_routes.api_collection(collection_id)
        listed = await collection_routes.api_user_collections()

        self.assertTrue(created["collection"]["smart"])
        self.assertEqual(created["collection"]["query"], {"flag": "picked", "sort": "elo"})
        self.assertEqual([image["id"] for image in detail["collection"]["images"]], [first, second])
        self.assertEqual(listed["collections"][0]["image_count"], 2)
        self.assertEqual(listed["collections"][0]["cover_image_id"], first)

        await db.set_image_flag(third, "picked")
        after_flag = await collection_routes.api_user_collections()

        self.assertEqual(after_flag["collections"][0]["image_count"], 3)
        self.assertEqual(after_flag["collections"][0]["cover_image_id"], third)

        renamed = await collection_routes.api_update_collection(
            collection_id,
            collection_routes.UpdateCollectionBody(name="Picked keepers"),
        )
        self.assertEqual(renamed["collection"]["name"], "Picked keepers")
        self.assertEqual(renamed["collection"]["query"], {"flag": "picked", "sort": "elo"})

        conflict = await collection_routes.api_add_collection_images(
            collection_id,
            collection_routes.CollectionImagesBody(image_ids=[third]),
        )
        self.assertEqual(conflict.status_code, 409)

        materialized = await collection_routes.api_update_collection(
            collection_id,
            collection_routes.UpdateCollectionBody(materialize=True),
        )
        self.assertFalse(materialized["collection"]["smart"])
        self.assertIsNone(materialized["collection"]["query"])
        self.assertEqual(materialized["collection"]["image_count"], 3)

        await db.set_image_flag(first, "unflagged")
        frozen = await collection_routes.api_collection(collection_id)

        self.assertFalse(frozen["collection"]["smart"])
        self.assertEqual([image["id"] for image in frozen["collection"]["images"]], [third, first, second])

    async def test_smart_collection_rejects_invalid_query_key(self):
        def probe():
            client = TestClient(app_module.app)
            try:
                return client.post(
                    "/api/user-collections",
                    json={"name": "Bad smart", "query": {"flag": "picked", "rating": 5}},
                )
            finally:
                client.close()

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 422)
        self.assertIn("Unknown smart collection query key", response.json()["detail"])

    async def test_smart_collection_with_no_matches_is_a_normal_empty_collection(self):
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="No matches",
                query={"q": "definitely-not-in-this-catalog"},
            )
        )
        collection_id = created["collection"]["id"]

        detail = await collection_routes.api_collection(collection_id)
        listed = await collection_routes.api_user_collections()
        summary = next(row for row in listed["collections"] if row["id"] == collection_id)

        self.assertEqual(detail["collection"]["image_count"], 0)
        self.assertEqual(detail["collection"]["images"], [])
        self.assertEqual(summary["image_count"], 0)
        self.assertIsNone(summary["cover_image_id"])

    async def test_unicode_and_emoji_names_roundtrip_through_scan_and_collection(self):
        source = await self._source("unicode-source")
        filename = "夏の旅 📷.jpg"
        filepath = os.path.join(source["path"], filename)
        with open(filepath, "wb") as handle:
            handle.write(b"unicode-photo")
        await scanner.scan_folder(source["path"], source_id=source["id"])
        conn = await db.get_db()
        try:
            image = await (await conn.execute(
                "SELECT id FROM images WHERE filepath = ?", (filepath,)
            )).fetchone()
        finally:
            await conn.close()

        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="京都の夏 🌸",
                image_ids=[int(image["id"])],
            )
        )
        detail = await collection_routes.api_collection(created["collection"]["id"])

        self.assertEqual(detail["collection"]["name"], "京都の夏 🌸")
        self.assertEqual(detail["collection"]["images"][0]["filename"], filename)

    async def test_smart_collection_rejects_oversize_string_fields(self):
        def probe():
            client = TestClient(app_module.app)
            try:
                q_response = client.post(
                    "/api/user-collections",
                    json={"name": "Bad smart", "query": {"q": "x" * 1001}},
                )
                folder_response = client.post(
                    "/api/user-collections",
                    json={"name": "Bad smart", "query": {"folder": "x" * 501}},
                )
                return q_response, folder_response
            finally:
                client.close()

        q_response, folder_response = await asyncio.to_thread(probe)

        self.assertEqual(q_response.status_code, 422)
        self.assertIn("query.q must be 1000 characters or less", q_response.json()["detail"])
        self.assertEqual(folder_response.status_code, 422)
        self.assertIn("query.folder must be 500 characters or less", folder_response.json()["detail"])

    async def test_smart_collection_rejects_folder_arrays(self):
        def probe():
            client = TestClient(app_module.app)
            try:
                return client.post(
                    "/api/user-collections",
                    json={"name": "Bad smart", "query": {"folder": ["/archive/a", "/archive/b"]}},
                )
            finally:
                client.close()

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 422)
        self.assertIn("query.folder must be a string", response.json()["detail"])

    async def test_smart_collection_materialize_cap_returns_conflict(self):
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name="Too broad", query={"flag": "picked"})
        )
        old_resolver = collection_routes._resolve_smart_materialized_image_ids

        async def too_many(_query):
            raise smart_collections.SmartCollectionMaterializeTooLarge(
                smart_collections.MAX_MATERIALIZE_IMAGE_IDS + 1
            )

        collection_routes._resolve_smart_materialized_image_ids = too_many
        try:
            response = await collection_routes.api_update_collection(
                created["collection"]["id"],
                collection_routes.UpdateCollectionBody(materialize=True),
            )
        finally:
            collection_routes._resolve_smart_materialized_image_ids = old_resolver

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.body.decode().count("10001"), 1)

    async def test_resolve_image_ids_caps_materialization_before_loading_rows(self):
        async def resolve_library_constraints(_q, *, people="", deep=False):
            return {"id_filter": None, "text_query": ""}

        async def count_rankings(**_kwargs):
            return smart_collections.MAX_MATERIALIZE_IMAGE_IDS + 1

        async def get_rankings(**_kwargs):
            raise AssertionError("materialize cap should stop before loading rows")

        with self.assertRaises(smart_collections.SmartCollectionMaterializeTooLarge):
            await smart_collections.resolve_materialized_image_ids(
                {"flag": "picked"},
                resolve_library_constraints=resolve_library_constraints,
                count_rankings=count_rankings,
                get_rankings=get_rankings,
            )

    async def test_collection_mutations_invalidate_suggestions_cache(self):
        source = await self._source()
        image_id = await self._image(source["id"], "member.jpg")

        def prime_cache():
            collection_suggestions._cache.update({
                "key": "test",
                "data": {"suggestions": [{"id": "stale"}]},
                "expires": time.monotonic() + 600,
            })

        def cache_empty():
            return collection_suggestions._cache["data"] is None

        prime_cache()
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name="Cache invalidation")
        )
        self.assertTrue(cache_empty())

        prime_cache()
        await collection_routes.api_add_collection_images(
            created["collection"]["id"],
            collection_routes.CollectionImagesBody(image_ids=[image_id]),
        )
        self.assertTrue(cache_empty())

        prime_cache()
        await collection_routes.api_remove_collection_images_post(
            created["collection"]["id"],
            collection_routes.CollectionImagesBody(image_ids=[image_id]),
        )
        self.assertTrue(cache_empty())

        prime_cache()
        await collection_routes.api_delete_collection(created["collection"]["id"])
        self.assertTrue(cache_empty())

    async def test_suggestion_cursor_tracks_catalog_and_caption_changes(self):
        source = await self._source()
        image_id = await self._image(source["id"], "cursor.jpg")
        model_key = settings.active_caption_config()["model_key"]

        initial = await collection_suggestions._suggestion_cursor(db.DB_PATH, model_key)
        await db.set_image_flag(image_id, "picked")
        catalog_changed = await collection_suggestions._suggestion_cursor(db.DB_PATH, model_key)
        await db.store_caption_result(
            image_id=image_id,
            caption_config=settings.active_caption_config(),
            caption="First caption.",
            tags=["first"],
            status="done",
        )
        caption_added = await collection_suggestions._suggestion_cursor(db.DB_PATH, model_key)
        await asyncio.sleep(0.001)
        await db.store_caption_result(
            image_id=image_id,
            caption_config=settings.active_caption_config(),
            caption="Replacement caption.",
            tags=["second"],
            status="done",
        )
        caption_replaced = await collection_suggestions._suggestion_cursor(db.DB_PATH, model_key)

        self.assertGreater(catalog_changed[0], initial[0])
        self.assertNotEqual(caption_added[1], catalog_changed[1])
        self.assertNotEqual(caption_replaced[1], caption_added[1])

    async def test_smart_collection_share_snapshots_membership(self):
        source = await self._source()
        first = await self._image(source["id"], "share-picked-a.jpg", elo=1400)
        second = await self._image(source["id"], "share-picked-b.jpg", elo=1300)
        third = await self._image(source["id"], "share-picked-c.jpg", elo=1500)
        await db.set_image_flag(first, "picked")
        await db.set_image_flag(second, "picked")
        templates = app_module.app.state.photoarchive_shell.templates
        share_routes.configure(
            templates=templates,
            create_or_rotate_share=lambda collection_id, **kwargs: db.create_or_rotate_share(
                collection_id,
                **kwargs,
            ),
            get_share=lambda collection_id: db.get_collection_share(collection_id),
            revoke_share=lambda collection_id: db.revoke_collection_share(collection_id),
            set_share_password=lambda collection_id, password_hash: db.set_collection_share_password(
                collection_id,
                password_hash,
            ),
            record_share_view=lambda token: db.record_share_view(token),
            resolve_token=lambda token: db.resolve_share_token(token),
            token_allows_image=lambda token, image_id: db.share_token_allows_image(token, image_id),
            set_favorite=lambda share_id, image_id, on, client_name=None, visitor_id="legacy": db.set_share_favorite(
                share_id,
                image_id,
                on,
                client_name=client_name,
                visitor_id=visitor_id,
            ),
            list_favorites=lambda share_id, visitor_id=None: db.list_share_favorites(
                share_id,
                visitor_id=visitor_id,
            ),
            favorites_for_collection=lambda collection_id: db.favorites_for_collection(collection_id),
            favorite_visitors_for_collection=lambda collection_id: db.favorite_visitors_for_collection(collection_id),
            thumbnail_response=lambda *_args, **_kwargs: Response(content=b"thumb", media_type="image/jpeg"),
            get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
            resolve_smart_image_ids=lambda query: smart_collections.resolve_image_ids(
                query,
                resolve_library_constraints=self._resolve_library_constraints,
                count_rankings=lambda **count_kwargs: db.count_rankings(**count_kwargs),
                get_rankings=lambda **ranking_kwargs: db.get_rankings(**ranking_kwargs),
            ),
        )
        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Share picked",
                query={"flag": "picked", "sort": "elo"},
            )
        )
        collection_id = created["collection"]["id"]

        share = await share_routes.api_create_share(
            collection_id,
            share_routes.ShareBody(),
            SimpleNamespace(base_url="http://testserver/"),
        )
        await db.set_image_flag(third, "picked")
        smart_detail = await collection_routes.api_collection(collection_id)
        resolved = await db.resolve_share_token(share["share"]["token"])

        self.assertEqual([image["id"] for image in smart_detail["collection"]["images"]], [third, first, second])
        self.assertEqual([image["id"] for image in resolved["images"]], [first, second])
        self.assertFalse(await db.share_token_allows_image(share["share"]["token"], third))

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

    async def test_theme_suggestions_from_active_caption_tags_and_pairs(self):
        source = await self._source()
        config = settings.active_caption_config()
        aurora_ids = []
        for index in range(14):
            image_id = await self._image(source["id"], f"plain-{index}.jpg", elo=1500 - index)
            aurora_ids.append(image_id)
            tags = ["aurora"]
            if index < 12:
                tags.append("winter")
            await db.store_caption_result(
                image_id=image_id,
                caption_config=config,
                caption="Captioned theme fixture.",
                tags=tags,
                status="done",
            )
        ignored = await self._image(source["id"], "old-model.jpg", elo=1800)
        await db.store_caption_result(
            image_id=ignored,
            caption_config={**config, "model_key": "old-caption-model"},
            caption="Old model should not count.",
            tags=["aurora"],
            status="done",
        )

        themes = await collection_suggestions._theme_suggestions(db.DB_PATH)
        by_title = {item["title"]: item for item in themes}

        self.assertIn("Aurora", by_title)
        self.assertIn("Aurora · Winter", by_title)
        self.assertEqual(by_title["Aurora"]["query"], {"tag": "aurora"})
        self.assertIsNone(by_title["Aurora · Winter"]["query"])
        self.assertEqual(by_title["Aurora"]["count"], 14)
        self.assertEqual(by_title["Aurora · Winter"]["count"], 12)
        self.assertEqual(by_title["Aurora"]["cover_image_id"], aurora_ids[0])

    async def test_theme_suggestions_require_minimum_and_captioned_share(self):
        source = await self._source()
        config = settings.active_caption_config()
        old_ratio = collection_suggestions.THEME_MIN_CAPTIONED_RATIO
        collection_suggestions.THEME_MIN_CAPTIONED_RATIO = 0.5
        try:
            for index in range(26):
                image_id = await self._image(source["id"], f"ratio-{index}.jpg")
                tags = ["blue sky"] if index < 12 else [f"filler-{index}"]
                await db.store_caption_result(
                    image_id=image_id,
                    caption_config=config,
                    caption="Coverage threshold fixture.",
                    tags=tags,
                    status="done",
                )

            themes = await collection_suggestions._theme_suggestions(db.DB_PATH)
        finally:
            collection_suggestions.THEME_MIN_CAPTIONED_RATIO = old_ratio

        self.assertNotIn("Blue sky", {item["title"] for item in themes})

    async def test_theme_suggestions_dedup_existing_collection_members(self):
        source = await self._source()
        config = settings.active_caption_config()
        image_ids = []
        for index in range(12):
            image_id = await self._image(source["id"], f"existing-theme-{index}.jpg")
            image_ids.append(image_id)
            await db.store_caption_result(
                image_id=image_id,
                caption_config=config,
                caption="Existing collection fixture.",
                tags=["aurora"],
                status="done",
            )
        await db.create_collection(name="Already saved", image_ids=image_ids)

        response = await collection_suggestions.collection_suggestions(
            db.DB_PATH,
            db_signature=f"{db.DB_PATH}:existing-theme",
        )

        self.assertNotIn("Aurora", {item["title"] for item in response["suggestions"]})

    async def test_theme_suggestion_accepts_as_smart_collection(self):
        source = await self._source()
        config = settings.active_caption_config()
        image_ids = []
        for index in range(12):
            image_id = await self._image(source["id"], f"IMG_{index:04d}.jpg", elo=1300 + index)
            image_ids.append(image_id)
            await db.store_caption_result(
                image_id=image_id,
                caption_config=config,
                caption="Smart accept fixture.",
                tags=["aurora"],
                status="done",
            )
        themes = await collection_suggestions._theme_suggestions(db.DB_PATH)
        theme = collection_suggestions._public_suggestion(
            next(item for item in themes if item["title"] == "Aurora")
        )

        created = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(name=theme["title"], query=theme["query"])
        )
        detail = await collection_routes.api_collection(created["collection"]["id"], limit=20)

        self.assertTrue(created["collection"]["smart"])
        self.assertEqual(created["collection"]["query"], {"tag": "aurora"})
        self.assertEqual({image["id"] for image in detail["collection"]["images"]}, set(image_ids))

    def test_theme_coverage_dampens_rank(self):
        low = collection_suggestions._coverage_rank_multiplier(captioned_count=10, total_count=1000)
        high = collection_suggestions._coverage_rank_multiplier(captioned_count=300, total_count=1000)

        self.assertLess(low, high)
        self.assertLess(low, 0.4)
        self.assertEqual(high, 1.0)


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
