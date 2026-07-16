"""Future-proof coverage for schema-driven permanent image deletion."""

from __future__ import annotations

from collections import defaultdict
from unittest.mock import patch

from test_support import *  # noqa: F401,F403
from data import connection as data_connection
from data.repositories import image_deletion
from features.library import keywords
from features.publishing import galleries
from features.quality import autocull
from features.sync import hub
from features.trash import service as trash_service


class TrashSchemaDeletionTests(BackendTestCase):
    async def _install_additive_feature_schema(self, conn) -> None:
        await autocull.ensure_autocull_tables(conn)
        await keywords.ensure_schema(conn)
        await galleries.ensure_tables(conn)
        await conn.executescript(hub.SYNC_DDL)
        await conn.commit()

    async def _image_foreign_keys(self, conn) -> list[dict]:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        relations: list[dict] = []
        for row in await cursor.fetchall():
            table = str(row["name"])
            foreign_keys = await (
                await conn.execute(f'PRAGMA foreign_key_list("{table}")')
            ).fetchall()
            grouped: dict[int, list] = defaultdict(list)
            for foreign_key in foreign_keys:
                if str(foreign_key["table"]) == "images":
                    grouped[int(foreign_key["id"])].append(foreign_key)
            for foreign_key_rows in grouped.values():
                ordered = sorted(foreign_key_rows, key=lambda item: int(item["seq"]))
                self.assertTrue(
                    all(str(item["to"]) == "id" for item in ordered),
                    f"{table} has an images FK that does not target images(id)",
                )
                self.assertEqual(
                    len(ordered),
                    1,
                    f"{table} has a composite images(id) FK; extend the generic seeder",
                )
                relations.append(
                    {
                        "table": table,
                        "column": str(ordered[0]["from"]),
                        "on_delete": str(ordered[0]["on_delete"] or "NO ACTION").upper(),
                    }
                )
        return relations

    async def _support_rows(self, conn, survivor_ids: list[int]) -> dict[tuple[str, str], object]:
        first_survivor, second_survivor = survivor_ids
        collection = await conn.execute(
            "INSERT INTO collections(name, cover_image_id) VALUES ('FK support', ?)",
            (first_survivor,),
        )
        collection_id = int(collection.lastrowid)
        gallery = await conn.execute(
            "INSERT INTO client_galleries "
            "(collection_id, token, title, cover_image_id, created_at, updated_at) "
            "VALUES (?, 'fk-support-gallery', 'FK support', ?, 1, 1)",
            (collection_id, first_survivor),
        )
        gallery_id = int(gallery.lastrowid)
        stack = await conn.execute(
            "INSERT INTO stacks(kind, representative_image_id, auto, created_at, updated_at) "
            "VALUES ('manual', ?, 0, 1, 1)",
            (first_survivor,),
        )
        stack_id = int(stack.lastrowid)
        await conn.executemany(
            "INSERT INTO stack_members(stack_id, image_id, score, added_at) VALUES (?, ?, ?, 1)",
            [(stack_id, first_survivor, 2.0), (stack_id, second_survivor, 1.0)],
        )
        history = await conn.execute(
            "INSERT INTO autocull_history(stack_id, picked_image_id, applied_at) VALUES (?, ?, 1)",
            (stack_id, first_survivor),
        )
        history_id = int(history.lastrowid)
        await conn.execute(
            "INSERT INTO embedding_models(model_key, model_id, revision, dimension) "
            "VALUES ('fk-support-model', 'test/model', 'test', 1)"
        )
        keyword = await conn.execute(
            "INSERT INTO keywords(name, created_at) VALUES ('fk-support-keyword', 'test')"
        )
        batch = await conn.execute(
            "INSERT INTO import_batches(name) VALUES ('FK support')"
        )
        person = await conn.execute("INSERT INTO people(name) VALUES ('FK support')")
        node = await conn.execute(
            "INSERT INTO published_nodes(area, slug, title) VALUES ('website', 'fk-support', 'FK support')"
        )
        share = await conn.execute(
            "INSERT INTO collection_shares(collection_id, token, created_at) "
            "VALUES (?, 'fk-support-share', 1)",
            (collection_id,),
        )
        await conn.commit()
        return {
            ("images", "id"): first_survivor,
            ("collections", "id"): collection_id,
            ("client_galleries", "id"): gallery_id,
            ("stacks", "id"): stack_id,
            ("autocull_history", "id"): history_id,
            ("embedding_models", "model_key"): "fk-support-model",
            ("keywords", "id"): int(keyword.lastrowid),
            ("import_batches", "id"): int(batch.lastrowid),
            ("people", "id"): int(person.lastrowid),
            ("published_nodes", "id"): int(node.lastrowid),
            ("collection_shares", "id"): int(share.lastrowid),
        }

    @staticmethod
    def _generic_value(table: str, column: str, declared_type: str, sequence: int):
        if (table, column) == ("stacks", "kind"):
            return "manual"
        prefix = f"fk-{table}-{column}-{sequence}"
        normalized_type = declared_type.upper()
        if "BLOB" in normalized_type:
            return prefix.encode()
        if "INT" in normalized_type:
            return sequence + 1
        if any(value in normalized_type for value in ("REAL", "FLOA", "DOUB", "NUM")):
            return float(sequence + 1)
        return prefix

    async def _seed_relation(
        self,
        conn,
        relation: dict,
        *,
        target_image_id: int,
        survivor_image_id: int,
        support_values: dict[tuple[str, str], object],
        sequence: int,
    ) -> int:
        table = str(relation["table"])
        target_column = str(relation["column"])
        columns = await (await conn.execute(f'PRAGMA table_info("{table}")')).fetchall()
        foreign_keys = await (
            await conn.execute(f'PRAGMA foreign_key_list("{table}")')
        ).fetchall()
        foreign_key_by_column = {str(row["from"]): row for row in foreign_keys}
        values: dict[str, object] = {}
        for column in columns:
            name = str(column["name"])
            foreign_key = foreign_key_by_column.get(name)
            if foreign_key is not None and str(foreign_key["table"]) == "images":
                values[name] = target_image_id if name == target_column else survivor_image_id
                continue
            if foreign_key is not None and int(column["notnull"]):
                key = (str(foreign_key["table"]), str(foreign_key["to"]))
                self.assertIn(
                    key,
                    support_values,
                    f"{table}.{name} needs a support row for {key}",
                )
                values[name] = support_values[key]
                continue
            if column["dflt_value"] is not None:
                continue
            if int(column["pk"]) and str(column["type"]).upper() == "INTEGER":
                continue
            if not int(column["notnull"]):
                continue
            values[name] = self._generic_value(table, name, str(column["type"]), sequence)

        quoted_columns = ", ".join(f'"{column}"' for column in values)
        placeholders = ", ".join("?" for _ in values)
        try:
            cursor = await conn.execute(
                f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
                tuple(values.values()),
            )
        except Exception as exc:
            self.fail(
                f"Could not seed newly discovered images FK {table}.{target_column}: {exc}"
            )
        return int(cursor.lastrowid)

    async def test_empty_trash_cleans_every_schema_declared_image_dependency(self):
        source = await self._source("schema-driven-delete")
        target_image_id = await self._image(int(source["id"]), "target.jpg")
        survivor_ids = [
            await self._image(int(source["id"]), "survivor-one.jpg"),
            await self._image(int(source["id"]), "survivor-two.jpg"),
        ]
        conn = await data_connection.open_async(db.DB_PATH)
        try:
            await self._install_additive_feature_schema(conn)
            await conn.execute(
                "UPDATE images SET status = 'trashed', trashed_at = 1, trash_path = NULL "
                "WHERE id = ?",
                (target_image_id,),
            )
            support_values = await self._support_rows(conn, survivor_ids)
            orphan_favorite = await conn.execute(
                "INSERT INTO share_favorites(share_id, image_id, client_name, created_at) "
                "VALUES (?, ?, 'Target favorite', 1)",
                (support_values[("collection_shares", "id")], target_image_id),
            )
            orphan_favorite_id = int(orphan_favorite.lastrowid)
            relations = await self._image_foreign_keys(conn)
            self.assertGreaterEqual(len(relations), 31)
            self.assertGreaterEqual(len({relation["table"] for relation in relations}), 30)

            seeded: list[tuple[dict, int]] = []
            target_history_id = None
            for sequence, relation in enumerate(relations, start=1):
                rowid = await self._seed_relation(
                    conn,
                    relation,
                    target_image_id=target_image_id,
                    survivor_image_id=survivor_ids[0],
                    support_values=support_values,
                    sequence=sequence,
                )
                seeded.append((relation, rowid))
                if relation["table"] == "autocull_history":
                    target_history_id = rowid

            self.assertIsNotNone(target_history_id)
            await conn.execute(
                "INSERT INTO autocull_history_items "
                "(history_id, image_id, previous_flag, applied_flag) VALUES (?, ?, 'unflagged', 'picked')",
                (target_history_id, survivor_ids[1]),
            )
            await conn.commit()
        finally:
            await data_connection.close_async(conn, db_path=db.DB_PATH)

        result = await trash_service.empty_trash(db.DB_PATH, image_ids=[target_image_id])

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["errors"], [])
        conn = await data_connection.open_async(db.DB_PATH)
        try:
            self.assertIsNone(
                await (await conn.execute("SELECT 1 FROM images WHERE id = ?", (target_image_id,))).fetchone()
            )
            for relation, rowid in seeded:
                table = str(relation["table"])
                column = str(relation["column"])
                row = await (
                    await conn.execute(f'SELECT * FROM "{table}" WHERE rowid = ?', (rowid,))
                ).fetchone()
                preserves_parent = relation["on_delete"] == "SET NULL" or (
                    table == "collections" and column == "cover_image_id"
                )
                if preserves_parent:
                    self.assertIsNotNone(row, f"{table}.{column} parent row was deleted")
                    self.assertIsNone(row[column], f"{table}.{column} still references the deleted image")
                else:
                    self.assertIsNone(row, f"{table}.{column} dependent row survived")

            self.assertIsNone(
                await (
                    await conn.execute(
                        "SELECT 1 FROM autocull_history_items WHERE history_id = ?",
                        (target_history_id,),
                    )
                ).fetchone(),
                "multi-hop autocull history dependent survived",
            )
            self.assertIsNone(
                await (
                    await conn.execute(
                        "SELECT 1 FROM share_favorites WHERE id = ?",
                        (orphan_favorite_id,),
                    )
                ).fetchone(),
                "share favorite without an image FK survived",
            )
            violations = await (await conn.execute("PRAGMA foreign_key_check")).fetchall()
            self.assertEqual(violations, [])
        finally:
            await data_connection.close_async(conn, db_path=db.DB_PATH)

    async def test_dependency_graph_cache_tracks_sqlite_schema_version(self):
        conn = await data_connection.open_async(db.DB_PATH)
        image_deletion._dependency_graph_cache.clear()
        try:
            with patch.object(
                image_deletion,
                "_introspect_foreign_keys",
                wraps=image_deletion._introspect_foreign_keys,
            ) as introspect:
                first = await image_deletion.dependency_graph(conn)
                second = await image_deletion.dependency_graph(conn)
                self.assertIs(first, second)
                self.assertEqual(introspect.await_count, 1)

                await conn.execute("CREATE TABLE schema_cache_probe (id INTEGER PRIMARY KEY)")
                third = await image_deletion.dependency_graph(conn)
                self.assertIsNot(first, third)
                self.assertEqual(introspect.await_count, 2)
        finally:
            await data_connection.close_async(conn, db_path=db.DB_PATH)
