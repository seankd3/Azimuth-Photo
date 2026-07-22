from test_support import *  # noqa: F401,F403
import asyncio
import contextlib
from unittest import mock

from features.people import routes as people_routes


class PeopleTests(BackendTestCase):
    async def test_people_status_never_cancels_slow_catalog_counts(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_counts():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"people": 8, "detected_faces": 42}

        people_routes.invalidate_people_status_cache()
        try:
            with mock.patch.object(
                people_routes,
                "_get_people_status_counts",
                side_effect=slow_counts,
            ):
                first = await people_routes.people_status_payload()
                await asyncio.wait_for(started.wait(), timeout=1)
                second = await people_routes.people_status_payload()

                self.assertTrue(first["status_stale"])
                self.assertTrue(second["status_stale"])
                self.assertEqual(calls, 1)

                release.set()
                for _ in range(20):
                    if not people_routes._people_status_counts_refreshing:
                        break
                    await asyncio.sleep(0)
                refreshed = await people_routes.people_status_payload()
                self.assertFalse(refreshed["status_stale"])
                self.assertEqual(refreshed["counts"]["detected_faces"], 42)
        finally:
            release.set()
            people_routes.invalidate_people_status_cache()

    async def test_people_immediate_claim_does_not_report_waiting(self):
        old_status = dict(face_worker._status)
        face_worker._set_status(state="scanning")
        try:
            with (
                mock.patch.object(
                    work_coordination,
                    "manual_turn_blocked",
                    return_value=False,
                ),
                mock.patch.object(
                    work_coordination,
                    "wait_for_manual_turn",
                    new=mock.AsyncMock(),
                ),
            ):
                await face_worker._wait_for_face_turn()

            self.assertEqual(face_worker.get_worker_status()["state"], "scanning")
        finally:
            face_worker._status.clear()
            face_worker._status.update(old_status)

    async def test_people_ownership_loss_unloads_before_reentering_wait(self):
        with (
            mock.patch.object(
                work_coordination,
                "lost_ownership",
                return_value=True,
            ),
            mock.patch.object(face_worker, "_unload_face_app") as unload_model,
            mock.patch.object(
                work_coordination,
                "wait_for_manual_turn",
                new=mock.AsyncMock(),
            ) as wait_for_manual,
        ):
            await face_worker._renew_face_turn()

        unload_model.assert_called_once_with()
        wait_for_manual.assert_awaited_once_with("people")

    async def test_people_retained_ownership_renews_manual_lease(self):
        with (
            mock.patch.object(
                work_coordination,
                "lost_ownership",
                return_value=False,
            ),
            mock.patch.object(face_worker, "_unload_face_app") as unload_model,
            mock.patch.object(
                work_coordination,
                "wait_for_manual_turn",
                new=mock.AsyncMock(),
            ) as wait_for_manual,
        ):
            await face_worker._renew_face_turn()

        unload_model.assert_not_called()
        wait_for_manual.assert_awaited_once_with("people")

    async def test_disabled_people_loop_releases_manual_owner(self):
        old_pause = face_worker._face_manual_pause
        old_pause_message = face_worker._face_manual_pause_message
        old_status = dict(face_worker._status)
        sleep_started = asyncio.Event()

        async def hold_sleep(_seconds):
            sleep_started.set()
            await asyncio.Future()

        face_worker._face_manual_pause = False
        face_worker._face_manual_pause_message = ""
        owner = work_coordination.manual_owner()
        if owner:
            work_coordination.release_manual_owner(owner)
        work_coordination.claim_manual_owner("people")
        task = None
        try:
            with (
                mock.patch.object(settings, "get_settings", return_value={
                    "people_scan_enabled": False,
                }),
                mock.patch.object(face_worker.asyncio, "sleep", side_effect=hold_sleep),
            ):
                task = asyncio.create_task(face_worker.run_face_worker())
                await asyncio.wait_for(sleep_started.wait(), timeout=1)
                self.assertIsNone(work_coordination.manual_owner())
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            face_worker._face_manual_pause = old_pause
            face_worker._face_manual_pause_message = old_pause_message
            face_worker._status.clear()
            face_worker._status.update(old_status)
            work_coordination.release_manual_owner("people")
    async def _request(self, method, path, **kwargs):
        from fastapi.testclient import TestClient

        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)

        return await asyncio.to_thread(send)

    async def _face(self, image_id, *, vector):
        scan = await db.store_face_scan_result(
            image_id=image_id,
            model_id="buffalo_l",
            cache_path=os.path.join(self.tempdir.name, f"face-{image_id}.jpg"),
            faces=[{
                "bbox": {"x": 10, "y": 12, "w": 34, "h": 42},
                "confidence": 0.95,
                "quality": 0.91,
                "embedding": np.array(vector, dtype=np.float32),
            }],
        )
        return scan["face_ids"][0]

    async def test_assign_face_http_attaches_only_target_face_to_existing_person(self):
        source = await self._source()
        target_face = await self._face(await self._image(source["id"], "target.jpg"), vector=[1.0, 0.0])
        assigned_face = await self._face(await self._image(source["id"], "assigned.jpg"), vector=[0.0, 1.0])
        person_id = (await db.assign_face(target_face))["person_id"]

        response = await self._request(
            "POST", f"/api/people/faces/{assigned_face}/assign", json={"person_id": person_id},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["person_id"], person_id)
        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT face_id FROM face_assignments WHERE person_id = ? AND active = 1 ORDER BY face_id",
                (person_id,),
            )).fetchall()
        finally:
            await conn.close()
        self.assertEqual([row["face_id"] for row in rows], sorted([target_face, assigned_face]))

    async def test_ignore_face_and_person_http_hide_reviews_without_deleting_originals(self):
        source = await self._source()
        face_image = await self._image(source["id"], "face.jpg")
        person_image = await self._image(source["id"], "person.jpg")
        for image_id, name in ((face_image, "face.jpg"), (person_image, "person.jpg")):
            with open(os.path.join(source["path"], name), "wb") as handle:
                handle.write(b"original")
        face_id = await self._face(face_image, vector=[1.0, 0.0])
        person_face_id = await self._face(person_image, vector=[0.0, 1.0])
        person_id = (await db.assign_face(person_face_id))["person_id"]

        ignored_face = await self._request("POST", f"/api/people/faces/{face_id}/ignore")
        ignored_person = await self._request("POST", f"/api/people/{person_id}/ignore")

        self.assertEqual(ignored_face.status_code, 200, ignored_face.text)
        self.assertEqual(ignored_person.status_code, 200, ignored_person.text)
        conn = await db.get_db()
        try:
            face = await (await conn.execute("SELECT ignored FROM face_detections WHERE id = ?", (face_id,))).fetchone()
            person = await (await conn.execute("SELECT status FROM people WHERE id = ?", (person_id,))).fetchone()
        finally:
            await conn.close()
        self.assertEqual(face["ignored"], 1)
        self.assertEqual(person["status"], "ignored")
        self.assertTrue(os.path.isfile(os.path.join(source["path"], "face.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(source["path"], "person.jpg")))

    async def test_reject_merge_suggestion_http_marks_only_that_suggestion_rejected(self):
        source = await self._source()
        first_face = await self._face(await self._image(source["id"], "one.jpg"), vector=[1.0, 0.0])
        second_face = await self._face(await self._image(source["id"], "two.jpg"), vector=[0.0, 1.0])
        first_person = (await db.assign_face(first_face))["person_id"]
        second_person = (await db.assign_face(second_face))["person_id"]
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO people_merge_suggestions (source_person_id, target_person_id, confidence, status, created_at, updated_at) VALUES (?, ?, .8, 'pending', 1, 1)",
                (first_person, second_person),
            )
            suggestion_id = cursor.lastrowid
            await conn.commit()
        finally:
            await conn.close()

        response = await self._request("POST", f"/api/people/merge-suggestions/{suggestion_id}/reject")

        self.assertEqual(response.status_code, 200, response.text)
        conn = await db.get_db()
        try:
            suggestion = await (await conn.execute(
                "SELECT status FROM people_merge_suggestions WHERE id = ?", (suggestion_id,),
            )).fetchone()
            people = await (await conn.execute(
                "SELECT id FROM people WHERE id IN (?, ?) AND status != 'merged'", (first_person, second_person),
            )).fetchall()
        finally:
            await conn.close()
        self.assertEqual(suggestion["status"], "rejected")
        self.assertEqual({row["id"] for row in people}, {first_person, second_person})
    async def test_people_review_uses_face_crop_thumbnail(self):
        from PIL import Image, ImageDraw

        source = await self._source()
        image_id = await self._image(source["id"], "group.jpg")
        preview_path = os.path.join(self.tempdir.name, "people-preview.jpg")
        preview = Image.new("RGB", (220, 140), (25, 25, 25))
        draw = ImageDraw.Draw(preview)
        draw.rectangle((30, 20, 80, 70), fill=(240, 220, 200))
        draw.rectangle((150, 20, 200, 70), fill=(40, 80, 160))
        preview.save(preview_path, "JPEG")

        scan = await db.store_face_scan_result(
            image_id=image_id,
            model_id="buffalo_l",
            cache_path=preview_path,
            faces=[
                {
                    "bbox": {"x": 30, "y": 20, "w": 50, "h": 50},
                    "confidence": 0.95,
                    "quality": 0.9,
                    "embedding": np.array([1.0, 0.0], dtype=np.float32),
                }
            ],
        )
        face_id = scan["face_ids"][0]
        await db.assign_face(face_id)

        review = await db.get_people_review(limit=4)
        people = (
            review["sections"]["most_seen"]
            + review["sections"]["named_people"]
            + review["sections"]["other_faces"]
        )
        self.assertEqual(len(people), 1)
        person = people[0]
        self.assertEqual(person["face_thumb_url"], f"/api/people/faces/{face_id}/thumb")
        self.assertEqual(person["image_thumb_url"], f"/api/thumb/sm/{image_id}")
        self.assertEqual(person["representative_bbox"]["x"], 30.0)

        response = await people_routes.api_people_face_thumb(HeaderRequest(), face_id, size=80)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "image/jpeg")
        face_image = Image.open(io.BytesIO(response.body))
        self.assertEqual(face_image.size, (80, 80))
        center_pixel = face_image.getpixel((40, 40))
        self.assertGreater(center_pixel[0], center_pixel[2])

    async def test_people_backlog_counts_cached_unscanned_images(self):
        source = await self._source(online=False)
        image_id = await self._image(source["id"], "offline-cached-unscanned.jpg")
        await self._cache_entry(image_id, "md")
        model_id = "buffalo_l"

        pending = await db.count_images_needing_faces(
            model_id=model_id,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(pending, 1)
        queued = await db.get_images_needing_faces(
            model_id=model_id,
            cache_root=thumbnails.SSD_CACHE_DIR,
            limit=4,
        )
        self.assertEqual([row["id"] for row in queued], [image_id])

        review = await db.get_people_review(limit=4)
        self.assertEqual(review["counts"]["pending_cached_images"], 1)

        await db.store_face_scan_result(
            image_id=image_id,
            model_id=model_id,
            cache_path=os.path.join(self.tempdir.name, "md-face-preview.jpg"),
            faces=[],
            status="scanned",
        )
        pending = await db.count_images_needing_faces(
            model_id=model_id,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(pending, 0)

        await db.store_face_scan_result(
            image_id=image_id,
            model_id=model_id,
            cache_path=os.path.join(self.tempdir.name, "md-face-preview.jpg"),
            faces=[],
            status="scanned",
        )
        review = await db.get_people_review(limit=4)
        self.assertEqual(review["counts"]["scan"]["scanned"], 1)

    def test_people_scan_decision_is_manual_bulk_work(self):
        decision = face_worker._people_background_decision({})

        self.assertFalse(decision.pause)
        self.assertEqual(decision.mode, "normal")
        self.assertEqual(decision.thumbnail_batch_size, 16)
        self.assertEqual(decision.embedding_pause_seconds, 0.0)
        self.assertEqual(decision.reason, "people scan enabled")
        self.assertEqual(decision.idle_policy, "manual_people_scan")
        self.assertTrue(decision.to_dict()["can_start_heavy_work"])
        self.assertNotIn("work_mode", decision.to_dict())

    async def test_people_merge_suggestions_include_face_thumbnails(self):
        source = await self._source()
        image_a = await self._image(source["id"], "source-face.jpg")
        image_b = await self._image(source["id"], "target-face.jpg")

        scan_a = await db.store_face_scan_result(
            image_id=image_a,
            model_id="buffalo_l",
            cache_path=os.path.join(self.tempdir.name, "source-face-preview.jpg"),
            faces=[
                {
                    "bbox": {"x": 10, "y": 12, "w": 34, "h": 42},
                    "confidence": 0.95,
                    "quality": 0.91,
                    "embedding": np.array([1.0, 0.0], dtype=np.float32),
                }
            ],
        )
        scan_b = await db.store_face_scan_result(
            image_id=image_b,
            model_id="buffalo_l",
            cache_path=os.path.join(self.tempdir.name, "target-face-preview.jpg"),
            faces=[
                {
                    "bbox": {"x": 40, "y": 30, "w": 38, "h": 44},
                    "confidence": 0.96,
                    "quality": 0.92,
                    "embedding": np.array([0.95, 0.05], dtype=np.float32),
                }
            ],
        )
        source_person = (await db.assign_face(scan_a["face_ids"][0]))["person_id"]
        target_person = (await db.assign_face(scan_b["face_ids"][0]))["person_id"]
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO people_merge_suggestions "
                "(source_person_id, target_person_id, confidence, status, created_at, updated_at) "
                "VALUES (?, ?, 0.74, 'pending', 1, 1)",
                (min(source_person, target_person), max(source_person, target_person)),
            )
            await conn.commit()
        finally:
            await conn.close()

        review = await db.get_people_review(limit=4)
        suggestion = review["sections"]["needs_review"][0]
        self.assertIn("source", suggestion)
        self.assertIn("target", suggestion)
        self.assertTrue(suggestion["source"]["face_thumb_url"].startswith("/api/people/faces/"))
        self.assertTrue(suggestion["target"]["face_thumb_url"].startswith("/api/people/faces/"))
        self.assertEqual(suggestion["confidence"], 0.74)

    async def test_people_merge_preserves_source_label_when_target_is_unnamed(self):
        source = await self._source()
        image_a = await self._image(source["id"], "labeled-face.jpg")
        image_b = await self._image(source["id"], "unnamed-face.jpg")

        scan_a = await db.store_face_scan_result(
            image_id=image_a,
            model_id="buffalo_l",
            cache_path=os.path.join(self.tempdir.name, "labeled-face-preview.jpg"),
            faces=[
                {
                    "bbox": {"x": 8, "y": 10, "w": 30, "h": 36},
                    "confidence": 0.95,
                    "quality": 0.9,
                    "embedding": np.array([1.0, 0.0], dtype=np.float32),
                }
            ],
        )
        scan_b = await db.store_face_scan_result(
            image_id=image_b,
            model_id="buffalo_l",
            cache_path=os.path.join(self.tempdir.name, "unnamed-face-preview.jpg"),
            faces=[
                {
                    "bbox": {"x": 18, "y": 20, "w": 34, "h": 40},
                    "confidence": 0.96,
                    "quality": 0.91,
                    "embedding": np.array([0.98, 0.02], dtype=np.float32),
                }
            ],
        )
        labeled_person = (await db.assign_face(scan_a["face_ids"][0]))["person_id"]
        unnamed_person = (await db.assign_face(scan_b["face_ids"][0]))["person_id"]
        await db.label_person(labeled_person, "Chrissy")

        result = await db.merge_people(labeled_person, unnamed_person)
        self.assertTrue(result["ok"])

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name, status FROM people WHERE id = ?",
                (unnamed_person,),
            )
            target = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT status, merged_into_person_id FROM people WHERE id = ?",
                (labeled_person,),
            )
            source_row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertEqual(target["name"], "Chrissy")
        self.assertEqual(target["status"], "named")
        self.assertEqual(source_row["status"], "merged")
        self.assertEqual(source_row["merged_into_person_id"], unnamed_person)
