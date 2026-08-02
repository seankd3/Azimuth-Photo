"""Contracts for Lightroom-readable XMP write-back."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest

import db
from pathlib import Path

from features.develop import xmp_write, xmp_write_routes


def torture_settings() -> dict[str, object]:
    return {
        "Exposure2012": 0.5,
        "Highlights2012": -25,
        "SharpenRadius": 1.25,
        "ConvertToGrayscale": False,
        "WhiteBalance": "Custom",
        "ToneCurvePV2012": ["0, 0", "128, 143", "255, 255"],
        "Look": {
            "Name": "Warm editorial",
            "Amount": 0.75,
            "Parameters": {
                "Clarity2012": 18,
                "ConvertToGrayscale": False,
                "ToneCurvePV2012": ["0, 0", "96, 112", "255, 255"],
            },
        },
        "MaskGroupBasedCorrections": [
            {
                "CorrectionID": "gradient-1",
                "CorrectionName": "Sky",
                "CorrectionActive": True,
                "CorrectionAmount": 1,
                "LocalExposure2012": -0.25,
                "CorrectionMasks": [
                    {
                        "What": "Mask/Gradient",
                        "MaskBlendMode": 0,
                        "MaskInverted": False,
                        "MaskValue": 0.85,
                        "ZeroX": 0.125,
                        "ZeroY": 0.2,
                        "FullX": 0.875,
                        "FullY": 0.65,
                        "CorrectionRangeMask": {
                            "Type": 1,
                            "LumRange": "0.10 0.25 0.75 0.90",
                        },
                    }
                ],
            },
            {
                "CorrectionID": "brush-1",
                "CorrectionActive": True,
                "CorrectionAmount": 0.9,
                "LocalTexture": 0.2,
                "CorrectionMasks": [
                    {
                        "What": "Mask/Paint",
                        "MaskBlendMode": 0,
                        "MaskInverted": True,
                        "MaskValue": 1,
                        "Flow": 0.7,
                        "CenterWeight": 0.35,
                        "Dabs": ["d 0.31 0.64", "r 0.13", "d 0.43 0.56"],
                    }
                ],
            },
        ],
    }


class XmpSerializationTests(unittest.TestCase):
    def test_torture_settings_round_trip_through_adobe_rdf(self):
        settings = torture_settings()
        serialized = xmp_write.serialize(settings)

        self.assertEqual(xmp_write.parse_xmp_text(serialized), settings)
        self.assertIn('crs:Exposure2012="+0.50"', serialized)
        self.assertIn('crs:Highlights2012="-25"', serialized)
        self.assertIn('crs:ConvertToGrayscale="False"', serialized)
        self.assertIn("<rdf:Seq>", serialized)
        self.assertIn("<crs:Look>", serialized)
        self.assertIn("<crs:MaskGroupBasedCorrections>", serialized)
        self.assertIn("<crs:CorrectionMasks>", serialized)


class XmpWriteTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "azimuth.db")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE images (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT NOT NULL,
                    vc_of INTEGER REFERENCES images(id),
                    hub_remote INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE develop_settings (
                    image_id INTEGER PRIMARY KEY, settings TEXT, origin TEXT,
                    xmp_path TEXT, xmp_mtime REAL, updated_at TEXT
                );
                """
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def _image(self, image_id: int, suffix: str, *, origin: str = "user", settings=None) -> Path:
        raw = self.root / f"image-{image_id}{suffix}"
        raw.write_bytes(b"raw fixture")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO images (id, filepath) VALUES (?, ?)", (image_id, str(raw)))
            conn.execute(
                "INSERT INTO develop_settings (image_id, settings, origin, updated_at) VALUES (?, ?, ?, 'now')",
                (image_id, json.dumps(settings or torture_settings()), origin),
            )
        return raw

    def test_sidecar_write_is_real_reimportable_and_idempotent(self):
        raw = self._image(1, ".cr3")

        first = xmp_write.write_image_xmp(self.db_path, 1)
        second = xmp_write.write_image_xmp(self.db_path, 1)

        sidecar = raw.with_suffix(".xmp")
        self.assertEqual(first["status"], "written")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(xmp_write.parse_xmp_text(sidecar.read_bytes()), torture_settings())

    def test_imported_unmodified_settings_are_skipped_with_note(self):
        raw = self._image(2, ".dng", origin="xmp")

        result = xmp_write.write_image_xmp(self.db_path, 2)

        self.assertEqual(result["status"], "skipped")
        self.assertIn("imported-unmodified", result["note"])
        self.assertFalse(raw.with_suffix(".xmp").exists())

    def test_virtual_copy_write_leaves_master_sidecar_untouched(self):
        raw = self._image(9, ".cr3", settings={"Exposure2012": 0.25})
        sidecar = raw.with_suffix(".xmp")
        sidecar.write_bytes(b"master sidecar")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO images (id, filepath, vc_of) VALUES (?, ?, ?)",
                (10, str(raw), 9),
            )
            conn.execute(
                "INSERT INTO develop_settings (image_id, settings, origin, updated_at) "
                "VALUES (?, ?, 'user', 'now')",
                (10, json.dumps({"Exposure2012": -1.5})),
            )

        result = xmp_write.write_image_xmp(self.db_path, 10)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(
            result["note"],
            "Virtual copies do not own the sidecar - write from the master.",
        )
        self.assertEqual(sidecar.read_bytes(), b"master sidecar")

    def test_embedded_dng_splice_preserves_size_and_backs_up_original_packet(self):
        settings = {"Exposure2012": 0.5, "ConvertToGrayscale": False}
        raw = self._image(3, ".dng", settings=settings)
        original_packet = (
            '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"><rdf:RDF><rdf:Description '
            'crs:Exposure2012="0"/></rdf:RDF></x:xmpmeta>\n'
            + (" " * 2048)
            + '<?xpacket end="w"?>'
        ).encode()
        raw.write_bytes(b"DNG-PREFIX" + original_packet + b"DNG-SUFFIX")
        before_size = raw.stat().st_size

        result = xmp_write.write_image_xmp(self.db_path, 3, mode="embedded")

        self.assertEqual(result["status"], "written")
        self.assertEqual(raw.stat().st_size, before_size)
        self.assertEqual(Path(f"{raw}.xmp-backup").read_bytes(), original_packet)
        embedded = xmp_write._embedded_packet(str(raw))
        self.assertEqual(xmp_write.parse_xmp_text(embedded.payload), settings)
        self.assertTrue(raw.read_bytes().endswith(b"DNG-SUFFIX"))

    def test_oversize_embedded_packet_falls_back_without_touching_dng(self):
        settings = {"CameraProfile": "x" * 5000}
        raw = self._image(4, ".dng", settings=settings)
        packet = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"><rdf:RDF><rdf:Description/></rdf:RDF>'
            '</x:xmpmeta>'
        ).encode()
        original = b"HEAD" + packet + b"TAIL"
        raw.write_bytes(original)

        result = xmp_write.write_image_xmp(self.db_path, 4, mode="embedded")

        self.assertEqual(result["status"], "fallback_sidecar")
        self.assertEqual(raw.read_bytes(), original)
        self.assertFalse(Path(f"{raw}.xmp-backup").exists())
        self.assertEqual(xmp_write.parse_xmp_text(raw.with_suffix(".xmp").read_bytes()), settings)

    def test_vendor_raw_write_back_targets_the_sidecar(self):
        settings = {"Exposure2012": 0.5}
        arw = self._image(11, ".arw", settings=settings)
        nef = self._image(12, ".nef", settings=settings)

        arw_result = xmp_write.write_image_xmp(self.db_path, 11)
        nef_result = xmp_write.write_image_xmp(self.db_path, 12, mode="embedded")

        self.assertEqual(arw_result["status"], "written")
        self.assertEqual(xmp_write.parse_xmp_text(arw.with_suffix(".xmp").read_bytes()), settings)
        # Embedded requests must never splice a non-DNG vendor raw.
        self.assertEqual(nef_result["status"], "fallback_sidecar")
        self.assertEqual(nef.read_bytes(), b"raw fixture")
        self.assertEqual(xmp_write.parse_xmp_text(nef.with_suffix(".xmp").read_bytes()), settings)

    def test_batch_deduplicates_and_reports_each_outcome(self):
        self._image(5, ".cr2", settings={"Exposure2012": -1})
        self._image(6, ".cr3", origin="lrcat", settings={"Exposure2012": 1})

        result = xmp_write.write_batch_xmp(self.db_path, [5, 5, 6, 999])

        self.assertEqual(result["requested"], 4)
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["skipped"], 2)

    def test_hub_remote_write_is_explicit_and_excluded_from_bulk_counts(self):
        remote = self._image(11, ".cr3", settings={"Exposure2012": 1.0})
        self._image(12, ".cr3", settings={"Exposure2012": -1.0})
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE images SET hub_remote = 1 WHERE id = 11")
        remote.unlink()

        individual = xmp_write.write_image_xmp(self.db_path, 11)
        batch = xmp_write.write_batch_xmp(self.db_path, [11, 12])

        self.assertEqual(individual["status"], "hub_remote")
        self.assertEqual(
            individual["note"],
            "This photo is mirrored from the hub; write XMP on the hub.",
        )
        self.assertEqual(batch["requested"], 2)
        self.assertEqual(batch["processed"], 1)
        self.assertEqual(batch["excluded_hub_remote"], 1)
        self.assertEqual(batch["written"], 1)
        self.assertEqual(batch["skipped"], 0)

    def test_api_contract_exposes_individual_mode_and_sidecar_batch(self):
        self._image(7, ".dng", settings={"Exposure2012": 0.5})
        self._image(8, ".cr3", settings={"Exposure2012": -0.5})
        db.DB_PATH = self.db_path

        individual = asyncio.run(
            xmp_write_routes.api_write_xmp(
                7,
                xmp_write_routes.XmpWriteBody(mode="sidecar"),
            )
        )
        batch = asyncio.run(
            xmp_write_routes.api_write_xmp_batch(
                xmp_write_routes.XmpBatchWriteBody(image_ids=[7, 8]),
            )
        )
        contracts = {(route.path, frozenset(route.methods or ())) for route in xmp_write_routes.router.routes}

        self.assertEqual(individual["status"], "written")
        self.assertEqual(batch["processed"], 2)
        self.assertIn(("/api/develop/{image_id}/write-xmp", frozenset({"POST"})), contracts)
        self.assertIn(("/api/develop/write-xmp/batch", frozenset({"POST"})), contracts)


if __name__ == "__main__":
    unittest.main()
