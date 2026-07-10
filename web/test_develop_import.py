import json
import os
import sqlite3
import tempfile
import unittest

from features.develop import importer


ATTR_XMP = """<?xpacket begin=''?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'
 xmlns:crs='http://ns.adobe.com/camera-raw-settings/1.0/'>
 <rdf:RDF><rdf:Description crs:Exposure2012='+0.50' crs:ConvertToGrayscale='True'
  crs:Highlights2012='-25' crs:WhiteBalance='Custom'/></rdf:RDF>
</x:xmpmeta>"""

ELEMENT_XMP = """<x:xmpmeta xmlns:x='adobe:ns:meta/' xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'
 xmlns:crs='http://ns.adobe.com/camera-raw-settings/1.0/'>
 <rdf:RDF><rdf:Description><crs:Exposure2012>+0.50</crs:Exposure2012>
 <crs:ConvertToGrayscale>False</crs:ConvertToGrayscale>
 <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>128, 140</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
 <crs:ToneCurvePV2012Red><rdf:Seq><rdf:li>0, 1</rdf:li><rdf:li>255, 250</rdf:li></rdf:Seq></crs:ToneCurvePV2012Red>
 </rdf:Description></rdf:RDF>
</x:xmpmeta>"""


class DevelopImporterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tempdir.name, "RAWS", "2024", "2024-02-06")
        os.makedirs(self.root)
        self.db_path = os.path.join(self.tempdir.name, "throwaway.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE catalog_sources (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, display_name TEXT, included INTEGER,
                    online INTEGER, image_count INTEGER DEFAULT 0, active_image_count INTEGER DEFAULT 0,
                    created_at REAL, last_scan_at REAL, last_seen_at REAL, removed_at REAL
                );
                CREATE TABLE images (
                    id INTEGER PRIMARY KEY, source_id INTEGER, filename TEXT, filepath TEXT UNIQUE,
                    status TEXT, file_ext TEXT, file_size INTEGER, file_modified_at REAL, missing_at REAL
                );
                CREATE TABLE develop_settings (
                    image_id INTEGER PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL DEFAULT 'user',
                    xmp_path TEXT, xmp_mtime REAL, updated_at TEXT NOT NULL
                );
                """
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def _raw_with_xmp(self, name="IMG_0001.dng", xmp=ATTR_XMP):
        raw_path = os.path.join(self.root, name)
        with open(raw_path, "wb") as handle:
            handle.write(b"raw fixture")
        xmp_path = os.path.splitext(raw_path)[0] + ".xmp"
        with open(xmp_path, "w", encoding="utf-8") as handle:
            handle.write(xmp)
        return raw_path, xmp_path

    def _setting_row(self, raw_path):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT ds.*, i.id AS image_id FROM develop_settings ds JOIN images i ON i.id = ds.image_id WHERE i.filepath = ?",
                (raw_path,),
            ).fetchone()

    def test_parses_attribute_and_element_xmp_forms_with_normalized_values(self):
        attributes = importer.parse_xmp_text(ATTR_XMP)
        elements = importer.parse_xmp_text(ELEMENT_XMP)

        self.assertEqual(attributes["Exposure2012"], 0.5)
        self.assertTrue(attributes["ConvertToGrayscale"])
        self.assertEqual(attributes["Highlights2012"], -25)
        self.assertEqual(elements["Exposure2012"], 0.5)
        self.assertFalse(elements["ConvertToGrayscale"])
        self.assertEqual(elements["ToneCurvePV2012"], ["0, 0", "128, 140", "255, 255"])
        self.assertEqual(elements["ToneCurvePV2012Red"], ["0, 1", "255, 250"])

    def test_rescan_is_mtime_gated_and_idempotent(self):
        raw_path, xmp_path = self._raw_with_xmp()
        first = importer.scan_raws(self.root, self.db_path)
        first_row = self._setting_row(raw_path)
        second = importer.scan_raws(self.root, self.db_path)
        second_row = self._setting_row(raw_path)

        self.assertEqual(first["status"]["imported"], 1)
        self.assertEqual(first["status"]["sidecars"], 1)
        self.assertEqual(second["status"]["imported"], 0)
        self.assertEqual(second["status"]["sidecars"], 0)
        self.assertEqual(second["status"]["skipped"], 1)
        self.assertEqual(first_row["settings"], second_row["settings"])

        updated = ATTR_XMP.replace("+0.50", "+1.25")
        with open(xmp_path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        os.utime(xmp_path, (first_row["xmp_mtime"] + 3, first_row["xmp_mtime"] + 3))
        changed = importer.scan_raws(self.root, self.db_path)
        self.assertEqual(changed["status"]["sidecars"], 1)
        self.assertEqual(json.loads(self._setting_row(raw_path)["settings"])["Exposure2012"], 1.25)

    def test_user_origin_is_never_clobbered(self):
        raw_path, xmp_path = self._raw_with_xmp()
        importer.scan_raws(self.root, self.db_path)
        row = self._setting_row(raw_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE develop_settings SET settings = ?, origin = 'user' WHERE image_id = ?",
                (json.dumps({"Exposure2012": -2}), row["image_id"]),
            )
        with open(xmp_path, "w", encoding="utf-8") as handle:
            handle.write(ATTR_XMP.replace("+0.50", "+3.00"))
        os.utime(xmp_path, (row["xmp_mtime"] + 4, row["xmp_mtime"] + 4))

        result = importer.scan_raws(self.root, self.db_path)
        protected = self._setting_row(raw_path)
        self.assertEqual(result["status"]["sidecars"], 0)
        self.assertEqual(result["status"]["skipped"], 1)
        self.assertEqual(protected["origin"], "user")
        self.assertEqual(json.loads(protected["settings"])["Exposure2012"], -2)
