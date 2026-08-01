"""Embeddings reach the satellite, so search works away from the hub."""

import asyncio
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

import db
from features.sync import embedding_sync

MODEL = "qwen3-vl-embedding-2b"
DIMENSION = 8


def vector(seed: float) -> bytes:
    return struct.pack(f"<{DIMENSION}f", *[seed + i for i in range(DIMENSION)])


class EmbeddingSyncTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.hub_path = str(root / "hub.db")
        self.satellite_path = str(root / "satellite.db")
        self.old_db_path = db.DB_PATH
        self._next_index = 0
        for path in (self.hub_path, self.satellite_path):
            db.DB_PATH = path
            asyncio.run(db.init_db())
        db.DB_PATH = self.old_db_path

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _seed_hub(self, count: int, *, model: str = MODEL) -> list[int]:
        conn = sqlite3.connect(self.hub_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES ('/hub', 'Hub')"
            )
            source_id = conn.execute("SELECT id FROM catalog_sources WHERE path='/hub'").fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO embedding_models(model_key, model_id, revision, dimension) "
                "VALUES (?, 'test/model', 'main', ?)",
                (model, DIMENSION),
            )
            ids = []
            for _ in range(count):
                index = self._next_index
                self._next_index += 1
                image_id = conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                    (source_id, f"{index}.jpg", f"/hub/{index}.jpg"),
                ).lastrowid
                conn.execute(
                    "INSERT INTO embeddings_by_model(model_key, image_id, embedding, dimension) "
                    "VALUES (?, ?, ?, ?)",
                    (model, image_id, vector(float(index)), DIMENSION),
                )
                ids.append(int(image_id))
            conn.commit()
            return ids
        finally:
            conn.close()

    def _mirror_onto_satellite(self, hub_ids: list[int]) -> None:
        conn = sqlite3.connect(self.satellite_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES ('hub://', 'Hub library')"
            )
            source_id = conn.execute("SELECT id FROM catalog_sources WHERE path='hub://'").fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO embedding_models(model_key, model_id, revision, dimension) "
                "VALUES (?, 'test/model', 'main', ?)",
                (MODEL, DIMENSION),
            )
            for hub_id in hub_ids:
                conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, status, hub_image_id, hub_remote) "
                    "VALUES (?, ?, ?, 'kept', ?, 1)",
                    (source_id, f"{hub_id}.jpg", f"/hub/{hub_id}.jpg", hub_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _puller(self, *, model: str = MODEL, dimension: int = DIMENSION) -> embedding_sync.EmbeddingPuller:
        async def request(method, url, **_kwargs):
            cursor = int(url.split("cursor=")[1].split("&")[0])
            limit = int(url.split("limit=")[1])
            body = await embedding_sync.export_page(self.hub_path, cursor=cursor, limit=limit)
            return 200, {}, body

        return embedding_sync.EmbeddingPuller(
            db_path=self.satellite_path, hub="http://hub", request=request,
            model_key=model, dimension=dimension,
        )

    def _satellite_vectors(self) -> dict[int, bytes]:
        conn = sqlite3.connect(self.satellite_path)
        try:
            return {
                int(row[0]): bytes(row[1])
                for row in conn.execute("SELECT image_id, embedding FROM embeddings_by_model")
            }
        finally:
            conn.close()

    def test_every_vector_reaches_the_satellite(self):
        hub_ids = self._seed_hub(7)
        self._mirror_onto_satellite(hub_ids)

        status = asyncio.run(self._puller().refresh())
        self.assertEqual(status["rows_applied"], 7, status)
        self.assertEqual(len(self._satellite_vectors()), 7)

    def test_the_bytes_survive_the_round_trip(self):
        hub_ids = self._seed_hub(3)
        self._mirror_onto_satellite(hub_ids)
        asyncio.run(self._puller().refresh())

        # Keyed by the hub's image id: local row ids differ between the two
        # catalogs, which is exactly what hub_image_id exists to bridge.
        hub = sqlite3.connect(self.hub_path)
        try:
            expected = {
                int(row[0]): bytes(row[1])
                for row in hub.execute("SELECT image_id, embedding FROM embeddings_by_model")
            }
        finally:
            hub.close()
        sat = sqlite3.connect(self.satellite_path)
        try:
            got = {
                int(row[0]): bytes(row[1])
                for row in sat.execute(
                    "SELECT i.hub_image_id, e.embedding FROM embeddings_by_model e "
                    "JOIN images i ON i.id = e.image_id"
                )
            }
        finally:
            sat.close()
        self.assertEqual(got, expected, "a vector must arrive byte-identical")

    def test_a_second_pass_transfers_nothing(self):
        hub_ids = self._seed_hub(5)
        self._mirror_onto_satellite(hub_ids)
        asyncio.run(self._puller().refresh())

        again = asyncio.run(self._puller().refresh())
        self.assertEqual(again["rows_applied"], 0, "the cursor should have remembered")

    def test_new_photos_are_picked_up_where_the_cursor_left_off(self):
        first = self._seed_hub(3)
        self._mirror_onto_satellite(first)
        asyncio.run(self._puller().refresh())

        more = self._seed_hub(2)
        self._mirror_onto_satellite(more)
        status = asyncio.run(self._puller().refresh())
        self.assertEqual(status["rows_applied"], 2)
        self.assertEqual(len(self._satellite_vectors()), 5)

    def test_a_vector_from_another_model_is_refused(self):
        hub_ids = self._seed_hub(4, model="some-other-model")
        self._mirror_onto_satellite(hub_ids)

        status = asyncio.run(self._puller().refresh())
        self.assertEqual(status["rows_applied"], 0)
        self.assertEqual(status["skipped_wrong_model"], 4)
        self.assertEqual(self._satellite_vectors(), {}, "a foreign vector is meaningless, not merely worse")

    def test_a_photo_the_mirror_has_not_reached_yet_is_skipped_not_lost(self):
        hub_ids = self._seed_hub(4)
        self._mirror_onto_satellite(hub_ids[:2])

        status = asyncio.run(self._puller().refresh())
        self.assertEqual(status["rows_applied"], 2)
        self.assertEqual(status["skipped_unknown_image"], 2)

        # Once the catalog mirror catches up, a pass from the start fills them
        # in — the vectors were skipped, never dropped.
        self._mirror_onto_satellite(hub_ids[2:])
        conn = sqlite3.connect(self.satellite_path)
        try:
            conn.execute("DELETE FROM sync_embedding_state")
            conn.commit()
        finally:
            conn.close()
        asyncio.run(self._puller().refresh())
        self.assertEqual(len(self._satellite_vectors()), 4)


if __name__ == "__main__":
    unittest.main()
