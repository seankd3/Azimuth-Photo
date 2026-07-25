import sqlite3
import tempfile
import unittest
from pathlib import Path

import intelligence_audit


SCHEMA = """
CREATE TABLE catalog_sources (id INTEGER PRIMARY KEY, included INTEGER NOT NULL);
CREATE TABLE images (
    id INTEGER PRIMARY KEY, source_id INTEGER, status TEXT,
    missing_at REAL, trashed_at REAL
);
CREATE TABLE embeddings_by_model (model_key TEXT, image_id INTEGER);
CREATE TABLE image_captions (model_key TEXT, image_id INTEGER);
CREATE TABLE caption_scan_images (
    model_key TEXT, image_id INTEGER, status TEXT, last_error TEXT
);
CREATE TABLE face_scan_images (model_id TEXT, image_id INTEGER);
CREATE TABLE face_detections (embedding_model TEXT, ignored INTEGER);
CREATE TABLE people (
    id INTEGER PRIMARY KEY, name TEXT, status TEXT,
    merged_into_person_id INTEGER, face_count INTEGER
);
CREATE TABLE people_merge_suggestions (status TEXT);
CREATE TABLE comparisons (id INTEGER PRIMARY KEY);
"""


class IntelligenceAuditTests(unittest.TestCase):
    def test_collects_coverage_failures_and_fragmentation_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            query_path = Path(temp_dir) / "queries.json"
            query_path.write_text(
                '{"version":1,"queries":[{"query":"cat","judgments":{"1":3}}]}',
                encoding="utf-8",
            )
            conn = sqlite3.connect(":memory:")
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO catalog_sources VALUES (1, 1)")
            conn.executemany(
                "INSERT INTO images VALUES (?, 1, 'kept', NULL, NULL)",
                [(1,), (2,), (3,)],
            )
            conn.executemany(
                "INSERT INTO embeddings_by_model VALUES ('embed', ?)",
                [(1,), (2,)],
            )
            conn.execute("INSERT INTO image_captions VALUES ('caption', 1)")
            conn.executemany(
                "INSERT INTO caption_scan_images VALUES ('caption', ?, ?, ?)",
                [(1, "done", ""), (2, "error", "CUDA out of memory")],
            )
            conn.executemany("INSERT INTO face_scan_images VALUES ('face', ?)", [(1,), (2,)])
            conn.executemany(
                "INSERT INTO face_detections VALUES (?, ?)",
                [("face", 0), ("face", 0)],
            )
            conn.executemany(
                "INSERT INTO people VALUES (?, ?, ?, NULL, ?)",
                [(1, "Maya", "named", 2), (2, "", "unknown", 1)],
            )
            conn.execute("INSERT INTO people_merge_suggestions VALUES ('pending')")
            conn.executemany("INSERT INTO comparisons VALUES (?)", [(1,), (2,)])

            health = intelligence_audit.collect_health(
                conn,
                embedding_model_key="embed",
                caption_model_key="caption",
                face_model_id="face",
                query_set=query_path,
            )

        self.assertEqual(health["library"]["active_images"], 3)
        self.assertEqual(health["search"]["coverage_pct"], 66.67)
        self.assertEqual(health["captions"]["oom_errors"], 1)
        self.assertEqual(health["captions"]["errors"], 0)
        self.assertEqual(health["captions"]["system_deferred"], 1)
        self.assertEqual(health["people"]["singleton_cluster_pct"], 50.0)
        self.assertEqual(health["search"]["judged_evaluation_queries"], 1)
        self.assertEqual(health["taste"]["direct_comparisons"], 2)


if __name__ == "__main__":
    unittest.main()
