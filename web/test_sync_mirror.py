"""FIELD_SPEC_V2 catalog-mirror acceptance coverage against a stub hub app."""

from __future__ import annotations

import gzip
import io
import json
import tarfile

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

import db
from features.sync.mirror import MirrorPuller
from features.sync.prefetch import ThumbPrefetcher
from test_support import BackendTestCase


class MirrorAcceptanceTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.rows = [
            {
                "hub_image_id": index,
                "content_hash": f"{index:032x}",
                "filename": f"hub-{index}.jpg",
                "filepath": f"/hub/2026/hub-{index}.jpg",
                "file_ext": ".jpg",
                "file_size": 1000 + index,
                "date_taken": f"2026-07-{(index % 28) + 1:02d}",
                "width": 2400,
                "height": 1600,
                "orientation": "landscape",
                "flag": "picked" if index == 2 else "unflagged",
                "elo": 1200 + index,
                "comparisons": index,
                "status": "kept",
                "keywords": ["Trips > Field"] if index == 1 else [],
            }
            for index in range(1, 201)
        ]
        self.client = TestClient(self._hub_app())

    async def asyncTearDown(self):
        self.client.close()
        await super().asyncTearDown()

    def _hub_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/api/sync/catalog/export")
        async def export(cursor: int = 0):
            payload = self.rows if cursor < 7 else []
            ndjson = "\n".join([*(json.dumps(row) for row in payload), json.dumps({"cursor": 7})]) + "\n"
            return Response(gzip.compress(ndjson.encode()), media_type="application/gzip")

        @app.get("/api/sync/thumbs/pack")
        async def pack(size: str, after_id: int = 0, limit: int = 500):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                for hub_id in range(max(1, after_id + 1), min(31, after_id + limit + 1)):
                    data = f"{size}-thumb-{hub_id}".encode()
                    info = tarfile.TarInfo(f"{hub_id}.jpg")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                trailer = json.dumps({"after_id": 30, "skipped": []}).encode()
                info = tarfile.TarInfo("trailer.json")
                info.size = len(trailer)
                archive.addfile(info, io.BytesIO(trailer))
            return Response(stream.getvalue(), media_type="application/x-tar")

        @app.get("/api/thumb/{size}/{hub_image_id}")
        async def thumb(size: str, hub_image_id: int):
            return Response(f"remote-{size}-{hub_image_id}".encode(), media_type="image/jpeg")

        return app

    async def _request(self, method, url, *, body=None, headers=None):
        response = self.client.request(method, url.removeprefix("http://hub"), content=body, headers=headers)
        return response.status_code, dict(response.headers), response.content

    def _mirror(self) -> MirrorPuller:
        return MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request)

    async def _repull(self, mirror: MirrorPuller) -> dict:
        """Re-read the whole export the way a satellite does after hub edits."""
        conn = await db.get_db()
        try:
            await conn.execute("DELETE FROM sync_mirror_state WHERE key = 'cursor'")
            await conn.commit()
        finally:
            await conn.close()
        return await mirror.refresh()

    async def _mirrored(self, hub_image_id: int) -> dict | None:
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT * FROM images WHERE hub_image_id = ?", (hub_image_id,)
            )).fetchone()
            return dict(row) if row is not None else None
        finally:
            await conn.close()

    async def _visible_count(self) -> int:
        # The catalog's own visibility predicate: a photo counts only while it
        # is kept and present.
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT COUNT(*) AS c FROM images "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
            )).fetchone()
            return int(row["c"])
        finally:
            await conn.close()

    async def test_v2_mirror_acceptance_scenario(self):
        source = await self._source("field-import")
        field_image = await self._image(source["id"], "already-local.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (self.rows[0]["content_hash"], field_image))
            await conn.commit()
        finally:
            await conn.close()

        mirror = MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request)
        first = await mirror.refresh()
        self.assertEqual(first["rows_applied"], 200)
        conn = await db.get_db()
        try:
            rows = await (await conn.execute("SELECT id, hub_image_id, hub_remote, missing_at FROM images ORDER BY hub_image_id")).fetchall()
            self.assertEqual(len(rows), 200)
            self.assertEqual(int(rows[0]["id"]), field_image)
            self.assertEqual(int(rows[0]["hub_image_id"]), 1)
            self.assertEqual(int(rows[0]["hub_remote"]), 0)
            self.assertTrue(all(row["missing_at"] is None for row in rows))
        finally:
            await conn.close()

        cached: dict[tuple[str, int], bytes] = {}
        prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=self._request,
            store=lambda size, image_id, _signature, data: cached.__setitem__((size, image_id), data),
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )
        packed = await prefetch.prefetch_once(size="sm")
        self.assertEqual(packed["cached"], 30)
        self.assertEqual(len(cached), 30)

        remote_image = next(row["id"] for row in rows if row["hub_image_id"] == 2)
        # This is the same single-item hub proxy path used by the media read-through seam.
        self.assertEqual(await prefetch.fetch_single("md", int(remote_image)), b"remote-md-2")
        second = await mirror.refresh()
        self.assertEqual(second["rows_applied"], 0)

    async def test_mirror_follows_hub_filepath_moves(self):
        """Hub paths can move (mount migrations, re-filed folders); the mirror must follow."""
        mirror = self._mirror()
        first = await mirror.refresh()
        self.assertEqual(first["rows_applied"], 200)

        moved = "/hub-moved/2026/hub-1.jpg"
        self.rows[0]["filepath"] = moved
        await self._repull(mirror)

        row = await self._mirrored(1)
        self.assertEqual(int(row["hub_remote"]), 1)
        self.assertEqual(row["filepath"], moved)

    async def test_mirror_keeps_its_path_when_the_hub_stops_sending_one(self):
        """An older hub omits filepath; that is silence, not an instruction to blank it."""
        mirror = self._mirror()
        await mirror.refresh()
        original = (await self._mirrored(1))["filepath"]

        self.rows[0].pop("filepath")
        await self._repull(mirror)

        self.assertEqual((await self._mirrored(1))["filepath"], original)

    async def test_mirror_follows_hub_retirement(self):
        """A photo the hub retires must stop counting here; the hub owns status."""
        mirror = self._mirror()
        await mirror.refresh()
        self.assertEqual(await self._visible_count(), 200)

        self.rows[0]["status"] = "removed"
        await self._repull(mirror)

        self.assertEqual((await self._mirrored(1))["status"], "removed")
        self.assertEqual(await self._visible_count(), 199)
        conn = await db.get_db()
        try:
            source = await (await conn.execute(
                "SELECT active_image_count FROM catalog_sources WHERE path = 'hub://'"
            )).fetchone()
        finally:
            await conn.close()
        self.assertEqual(int(source["active_image_count"]), 199)

    async def test_mirror_follows_hub_missing_at(self):
        """A photo the hub knows is missing must not stay visible on the satellite."""
        mirror = self._mirror()
        await mirror.refresh()
        self.assertIsNone((await self._mirrored(2))["missing_at"])

        self.rows[1]["missing_at"] = 1753900000.0
        await self._repull(mirror)

        self.assertEqual(float((await self._mirrored(2))["missing_at"]), 1753900000.0)
        self.assertEqual(await self._visible_count(), 199)

    async def test_local_import_keeps_its_own_file_when_it_gains_a_hub_identity(self):
        """A field import only gains the hub identity; its own file stays its truth."""
        source = await self._source("field-import")
        field_image = await self._image(source["id"], "already-local.jpg")
        local_path = (await self._image_row(field_image))["filepath"]
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = ? WHERE id = ?",
                (self.rows[0]["content_hash"], field_image),
            )
            await conn.commit()
        finally:
            await conn.close()
        # The hub's copy moved and then went missing. The local file did neither.
        self.rows[0]["filepath"] = "/hub-moved/2026/hub-1.jpg"
        self.rows[0]["missing_at"] = 1753900000.0

        await self._mirror().refresh()

        row = await self._image_row(field_image)
        self.assertEqual(int(row["hub_image_id"]), 1)
        self.assertEqual(int(row["hub_remote"]), 0)
        self.assertEqual(row["filepath"], local_path)
        self.assertEqual(int(row["source_id"]), int(source["id"]))
        self.assertIsNone(row["missing_at"])

    async def test_taken_filepath_skips_one_row_without_aborting_the_page(self):
        """One unusable path must never cost the rest of the page."""
        mirror = self._mirror()
        await mirror.refresh()

        held = self.rows[1]["filepath"]
        self.rows[0]["filepath"] = held  # a move into a path another row still holds
        moved = "/hub/2027/hub-3-moved.jpg"
        self.rows[2]["filepath"] = moved  # a legal move later in the same page
        self.rows.append({  # a new photo whose path is already taken
            "hub_image_id": 900,
            "content_hash": f"{900:032x}",
            "filename": "collides.jpg",
            "filepath": self.rows[4]["filepath"],
            "file_ext": ".jpg",
            "status": "kept",
        })

        result = await self._repull(mirror)

        self.assertEqual((await self._mirrored(1))["filepath"], "/hub/2026/hub-1.jpg")
        self.assertEqual((await self._mirrored(2))["filepath"], held)
        self.assertEqual((await self._mirrored(3))["filepath"], moved)
        self.assertEqual((await self._mirrored(5))["filepath"], self.rows[4]["filepath"])
        self.assertIsNone(await self._mirrored(900))
        self.assertEqual(result["skipped_conflicts"], 2)
        conn = await db.get_db()
        try:
            count = await (await conn.execute("SELECT COUNT(*) AS c FROM images")).fetchone()
        finally:
            await conn.close()
        self.assertEqual(int(count["c"]), 200)

    async def test_a_hub_photo_without_a_hash_still_mirrors(self):
        """3a113eb1: dropping unhashed rows hid 97,471 photos from the laptop.

        hub_image_id is the identity. A hash is only how a photo already
        imported locally is recognised as the same one, so a hub photo without
        one mirrors fine — it simply cannot adopt a local copy.
        """
        self.rows = [
            {
                "hub_image_id": index,
                "content_hash": content_hash,
                "filename": f"{name}.jpg",
                "filepath": f"/hub/{name}.jpg",
                "file_ext": ".jpg",
                "status": "kept",
            }
            for index, (name, content_hash) in enumerate(
                [("hashed", "a" * 32), ("missing-hash", ""), ("null-hash", None)], start=1
            )
        ]
        result = await MirrorPuller(
            db_path=db.DB_PATH, hub="http://hub", request=self._request
        ).refresh()

        self.assertEqual(result["rows_applied"], 3)
        conn = await db.get_db()
        try:
            rows = await (
                await conn.execute(
                    "SELECT hub_image_id, filename FROM images ORDER BY hub_image_id"
                )
            ).fetchall()
        finally:
            await conn.close()
        # Three rows, not two: an absent hash must not match another absent
        # hash, or the second unhashed photo adopts the first.
        self.assertEqual(
            [(int(row["hub_image_id"]), str(row["filename"])) for row in rows],
            [(1, "hashed.jpg"), (2, "missing-hash.jpg"), (3, "null-hash.jpg")],
        )


class MirrorDevelopGuardTests(BackendTestCase):
    async def test_mirror_develop_preserves_local_rating(self):
        source = await self._source()
        image_id = await self._image(source["id"], "rated.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, ?, 'user', '')",
                (image_id, json.dumps({"Exposure2012": 0.25, "_lr_rating": 4})),
            )
            await MirrorPuller._apply_develop(conn, image_id, {
                "develop_settings": {"Exposure2012": 1.0},
                "develop_updated_at": "2026-07-16T02:00:00Z",
                "develop_origin": "hub",
            })
            await conn.commit()
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
        finally:
            await conn.close()

        self.assertEqual(json.loads(row["settings"]), {"Exposure2012": 1.0, "_lr_rating": 4})

    async def test_mirror_never_rewinds_newer_local_develop_regardless_of_origin(self):
        from features.sync.mirror import MirrorPuller

        source = await self._source()
        image_id = await self._image(source["id"], "guarded.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, '{\"Exposure2012\":1.0}', 'sync', '2026-07-15T10:00:00Z')",
                (image_id,),
            )
            await conn.commit()
            # Older hub snapshot must not clobber the newer oplog-applied local row.
            await MirrorPuller._apply_develop(conn, image_id, {
                "develop_settings": {"Exposure2012": -5.0},
                "develop_updated_at": "2026-07-14T10:00:00Z",
                "develop_origin": "hub",
            })
            await conn.commit()
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
            self.assertIn(chr(34) + "Exposure2012" + chr(34) + ":1.0", row["settings"])
            # A genuinely newer hub snapshot still applies.
            await MirrorPuller._apply_develop(conn, image_id, {
                "develop_settings": {"Exposure2012": 2.5},
                "develop_updated_at": "2026-07-16T10:00:00Z",
                "develop_origin": "hub",
            })
            await conn.commit()
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
            self.assertIn(chr(34) + "Exposure2012" + chr(34) + ":2.5", row["settings"])
        finally:
            await conn.close()

    async def test_mirror_develop_consults_newer_family_clock(self):
        from features.sync import family_clock

        source = await self._source()
        image_id = await self._image(source["id"], "family-clock-guarded.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = ? WHERE id = ?",
                ("f" * 32, image_id),
            )
            content_hash = str((await (await conn.execute(
                "SELECT content_hash FROM images WHERE id = ?", (image_id,)
            )).fetchone())["content_hash"])
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, '{\"Exposure2012\":1.0}', 'sync', '2026-07-16T02:00:00Z')",
                (image_id,),
            )
            await family_clock.record_state(
                conn,
                content_hash,
                "develop",
                family_clock.legacy_key("2026-07-16T05:00:00Z"),
            )
            await MirrorPuller._apply_develop(conn, image_id, {
                "develop_settings": {"Exposure2012": -2.0},
                "develop_updated_at": "2026-07-16T04:00:00Z",
                "develop_origin": "hub",
            })
            row = await (await conn.execute(
                "SELECT settings, updated_at FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
        finally:
            await conn.close()

        self.assertEqual(json.loads(row["settings"]), {"Exposure2012": 1.0})
        self.assertEqual(row["updated_at"], "2026-07-16T02:00:00Z")


def test_the_mirror_applies_where_a_photo_sits_in_the_library():
    """A satellite cannot draw folders from structure it never receives."""

    from features.sync import mirror

    assert "relative_path" in mirror._IMAGE_COLUMNS


def test_relative_path_is_content_not_identity():
    """filepath and the hub ids are decided by _apply_row; this rides the copier."""

    from features.sync import mirror

    assert "relative_path" not in {"hub_image_id", "hub_remote", "filepath"}
    assert "relative_path" in mirror._IMAGE_COLUMNS


class LocalTrashSurvivesTheMirrorTests(BackendTestCase):
    """Deleting on the laptop has to stick.

    The hub's copy of a row still says kept until the satellite's status oplog
    entry reaches it. The mirror used to copy that back over the local row, so
    a photo trashed on the laptop reappeared on the next refresh.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.row = {
            "hub_image_id": 1,
            "content_hash": f"{1:032x}",
            "filename": "hub-1.jpg",
            "filepath": "/hub/2026/hub-1.jpg",
            "file_ext": ".jpg",
            "file_size": 1001,
            "date_taken": "2026-07-02",
            "width": 2400,
            "height": 1600,
            "orientation": "landscape",
            "flag": "unflagged",
            "elo": 1201,
            "comparisons": 1,
            "status": "kept",
            "keywords": [],
        }
        app = FastAPI()

        @app.get("/api/sync/catalog/export")
        async def export(cursor: int = 0):
            payload = [self.row] if cursor < 7 else []
            lines = [*(json.dumps(r) for r in payload), json.dumps({"cursor": 7})]
            ndjson = chr(10).join(lines) + chr(10)
            return Response(gzip.compress(ndjson.encode()), media_type="application/gzip")

        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await super().asyncTearDown()

    async def _request(self, method, url, *, body=None, headers=None):
        response = self.client.request(method, url.removeprefix("http://hub"), content=body, headers=headers)
        return response.status_code, dict(response.headers), response.content

    async def _refresh(self) -> None:
        """Re-read the whole export, the way a satellite does after hub edits."""
        conn = await db.get_db()
        try:
            await conn.execute("DELETE FROM sync_mirror_state WHERE key = 'cursor'")
            await conn.commit()
        except Exception:
            pass  # first run: the mirror creates the table itself
        await MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request).refresh()

    async def _status(self) -> str:
        conn = await db.get_db()
        row = await (await conn.execute("SELECT status FROM images LIMIT 1")).fetchone()
        return str(row["status"] or "") if row else ""

    async def _trash_locally(self) -> None:
        conn = await db.get_db()
        await conn.execute("UPDATE images SET status = 'trashed', trashed_at = 1")
        await conn.commit()

    async def test_a_refresh_does_not_untrash_what_the_user_trashed_here(self):
        await self._refresh()
        self.assertEqual(await self._status(), "kept")
        await self._trash_locally()
        # The hub has not heard yet; its row still says kept.
        await self._refresh()
        self.assertEqual(await self._status(), "trashed", "the mirror reverted a local trash")

    async def test_the_hub_can_still_retire_a_photo(self):
        await self._refresh()
        self.assertEqual(await self._status(), "kept")
        self.row["status"] = "trashed"
        self.row["trashed_at"] = 99.0
        await self._refresh()
        self.assertEqual(await self._status(), "trashed")

    async def test_trashing_records_a_status_entry_for_the_hub_to_pull(self):
        from features.trash import service as trash_service

        await self._refresh()
        conn = await db.get_db()
        image = await (await conn.execute("SELECT id FROM images LIMIT 1")).fetchone()
        await trash_service.trash_images(db.DB_PATH, [int(image["id"])])

        conn = await db.get_db()
        rows = await (
            await conn.execute(
                "SELECT payload FROM oplog WHERE family = 'status'"
            )
        ).fetchall()
        self.assertEqual(len(rows), 1, "trashing wrote no status entry")
        self.assertEqual(json.loads(rows[0]["payload"])["value"], "trashed")

