import asyncio
import json
import os
import tempfile
import unittest
from fractions import Fraction

from PIL import Image

from test_support import BackendTestCase, db
from features.library import geodata
from features.library.timeline_import import parse_timeline_file, parse_timeline_payload


class GeoDataTests(unittest.TestCase):
    def test_pillow_exif_gps_is_signed_and_invalid_origin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "gps.jpg")
            exif = Image.Exif()
            exif[0x8825] = {
                1: "N", 2: (Fraction(41), Fraction(30), Fraction(0)),
                3: "W", 4: (Fraction(87), Fraction(45), Fraction(0)),
            }
            Image.new("RGB", (2, 2)).save(path, exif=exif)
            metadata = geodata.extract_file_metadata(path)
        self.assertEqual((metadata["latitude"], metadata["longitude"]), (41.5, -87.75))
        self.assertIsNone(geodata.validate_coordinates(0, 0))
        self.assertIsNone(geodata.validate_coordinates(91, 0))


    def test_derived_locations_never_claim_exif_provenance(self):
        trail = [{"ts": 0, "lat": 41.0, "lon": -87.0, "source": "exif"}]
        self.assertEqual(geodata.infer_location(300, trail), (41.0, -87.0, "inferred"))
        self.assertEqual(geodata.infer_location(0, trail), (41.0, -87.0, "inferred"))
        timeline_trail = [{"ts": 0, "lat": 41.0, "lon": -87.0, "source": "timeline"}]
        self.assertEqual(geodata.infer_location(300, timeline_trail), (41.0, -87.0, "timeline"))

    def test_naive_date_taken_parses_as_local_wall_time(self):
        from datetime import datetime

        ts = geodata.parse_taken_timestamp("2024-06-01 15:00:00")
        self.assertEqual(ts, datetime(2024, 6, 1, 15, 0, 0).timestamp())
        utc = geodata.parse_taken_timestamp("2024-06-01T15:00:00Z")
        self.assertEqual(utc, 1717254000.0)

    def test_missing_hemisphere_ref_rejects_gps(self):
        gps = {2: (Fraction(41), Fraction(30), Fraction(0)), 3: "W", 4: (Fraction(87), Fraction(45), Fraction(0))}
        self.assertIsNone(geodata.parse_gps_ifd(gps))

    def test_location_priority_never_downgrades_existing_coordinates(self):
        self.assertFalse(geodata.location_can_replace("exif", "timeline", has_coordinates=True))
        self.assertTrue(geodata.location_can_replace("timeline", "exif", has_coordinates=True))
        self.assertFalse(geodata.location_can_replace(None, "exif", has_coordinates=True))
        self.assertTrue(geodata.location_can_replace(None, "exif", has_coordinates=False))

    def test_interpolation_windows_distance_and_one_side_edges(self):
        trail = [
            {"ts": 0, "lat": 41.0, "lon": -87.0, "source": "exif"},
            {"ts": 600, "lat": 41.1, "lon": -87.0, "source": "timeline"},
        ]
        self.assertEqual(geodata.infer_location(300, trail), (41.05, -87.0, "timeline"))
        self.assertEqual(geodata.infer_location(700, trail), (41.1, -87.0, "timeline"))
        self.assertIsNone(geodata.infer_location(1600, trail))
        far = [{"ts": 0, "lat": 0.1, "lon": 0.1, "source": "exif"}, {"ts": 600, "lat": 45.0, "lon": 45.0, "source": "exif"}]
        self.assertIsNone(geodata.infer_location(300, far))

    def test_timeline_import_reads_modern_and_legacy_formats(self):
        modern = {
            "semanticSegments": [{"startTime": "2024-01-01T00:00:00Z", "timelinePath": [
                {"time": "2024-01-01T00:00:10Z", "point": "geo:41.5,-87.75"}
            ]}]
        }
        legacy = {"locations": [{"timestampMs": "1704067200000", "latitudeE7": 415000000, "longitudeE7": -877500000}]}
        self.assertEqual(parse_timeline_payload(modern)[0][1:], (41.5, -87.75))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Records.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)
            self.assertEqual(parse_timeline_file(path)[0][1:], (41.5, -87.75))

    def test_backfill_fills_gps_and_null_metadata_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "camera.jpg")
            exif = Image.Exif()
            exif[271], exif[272] = "Nikon", "Nikon Zf"
            exif[0x8825] = {1: "N", 2: (Fraction(41), Fraction(0), Fraction(0)), 3: "W", 4: (Fraction(87), Fraction(0), Fraction(0))}
            Image.new("RGB", (2, 2)).save(path, exif=exif)
            db_path = os.path.join(directory, "geo.db")
            self._make_geo_db(db_path, path)
            _, changes = asyncio.run(geodata.backfill_batch(db_path))
            self.assertEqual(changes["gps"], 1)
            import sqlite3
            probe = sqlite3.connect(db_path)
            try:
                row = probe.execute("SELECT latitude, longitude, location_source, camera_make, camera_model FROM images").fetchone()
            finally:
                probe.close()
            self.assertEqual(row, (41.0, -87.0, "exif", "Nikon", "Zf"))

    @staticmethod
    def _make_geo_db(path, image_path):
        import sqlite3
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, filepath TEXT, latitude REAL, longitude REAL, location_source TEXT, date_taken TEXT, camera_make TEXT, camera_model TEXT, lens TEXT)")
        conn.execute("INSERT INTO images(id, filepath) VALUES (1, ?)", (image_path,))
        conn.commit()
        conn.close()


class GeoTimelineImportRouteTests(BackendTestCase):
    async def _request(self, method, path, **kwargs):
        from fastapi.testclient import TestClient

        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)

        return await asyncio.to_thread(send)

    async def test_timeline_import_http_fills_missing_gps_without_downgrading_exif(self):
        source = await self._source()
        inferred_id = await self._image(source["id"], "timeline.jpg")
        exif_id = await self._image(source["id"], "camera-gps.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ?, latitude = ?, longitude = ?, location_source = ? WHERE id = ?",
                ("2024-01-01T00:00:10Z", 41.5, -87.75, "exif", exif_id),
            )
            await conn.execute(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                ("2024-01-01T00:00:10Z", inferred_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        timeline_path = os.path.join(self.tempdir.name, "Records.json")
        with open(timeline_path, "w", encoding="utf-8") as handle:
            json.dump({"locations": [{
                "timestampMs": "1704067210000",
                "latitudeE7": 415000000,
                "longitudeE7": -877500000,
            }]}, handle)

        response = await self._request("POST", "/api/geo/timeline/import", json={"path": timeline_path})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["points_imported"], 1)
        self.assertEqual(response.json()["inference"]["timeline"], 1)
        inferred = await self._image_row(inferred_id)
        exif = await self._image_row(exif_id)
        self.assertEqual(
            (inferred["latitude"], inferred["longitude"], inferred["location_source"]),
            (41.5, -87.75, "timeline"),
        )
        self.assertEqual(
            (exif["latitude"], exif["longitude"], exif["location_source"]),
            (41.5, -87.75, "exif"),
        )


if __name__ == "__main__":
    unittest.main()
