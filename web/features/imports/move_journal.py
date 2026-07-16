"""Durable intent records for catalog moves that mutate the filesystem."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS taxonomy_move_journal (
    id INTEGER PRIMARY KEY,
    image_id INTEGER NOT NULL,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    new_source_id INTEGER,
    created_at TEXT NOT NULL
);
"""


async def ensure_table(conn) -> None:
    """Install the additive journal table when the repair is first used."""
    await conn.executescript(DDL)


async def record_intent(
    conn,
    *,
    image_id: int,
    old_path: Path,
    new_path: Path,
    new_source_id: int | None,
) -> None:
    """Persist a move intent before the corresponding filesystem mutation."""
    await conn.execute(
        """INSERT INTO taxonomy_move_journal
           (image_id, old_path, new_path, new_source_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            image_id,
            str(old_path),
            str(new_path),
            new_source_id,
            datetime.now(UTC).isoformat(),
        ),
    )
    await conn.commit()


async def apply_and_clear(
    conn,
    *,
    image_id: int,
    new_path: Path,
    new_source_id: int | None,
) -> None:
    """Commit the catalog side of a completed move and clear its intent."""
    await conn.execute(
        "UPDATE images SET filepath = ?, source_id = ? WHERE id = ?",
        (str(new_path), new_source_id, image_id),
    )
    await conn.execute(
        "DELETE FROM taxonomy_move_journal WHERE image_id = ? AND new_path = ?",
        (image_id, str(new_path)),
    )
    await conn.commit()


async def recover(conn) -> dict[str, int]:
    """Reconcile durable move intents with the filesystem after an interruption."""
    await ensure_table(conn)
    result = {"redone": 0, "undone": 0, "ambiguous": 0, "lost": 0}
    rows = await (
        await conn.execute(
            """SELECT id, image_id, old_path, new_path, new_source_id
               FROM taxonomy_move_journal ORDER BY id"""
        )
    ).fetchall()
    for row in rows:
        old_path = Path(row["old_path"])
        new_path = Path(row["new_path"])
        old_exists = old_path.exists()
        new_exists = new_path.exists()

        if old_exists and new_exists:
            result["ambiguous"] += 1
            continue
        if new_exists:
            await conn.execute(
                "UPDATE images SET filepath = ?, source_id = ? WHERE id = ?",
                (str(new_path), row["new_source_id"], row["image_id"]),
            )
            await conn.execute("DELETE FROM taxonomy_move_journal WHERE id = ?", (row["id"],))
            await conn.commit()
            result["redone"] += 1
        elif old_exists:
            await conn.execute("DELETE FROM taxonomy_move_journal WHERE id = ?", (row["id"],))
            await conn.commit()
            result["undone"] += 1
        else:
            await conn.execute("DELETE FROM taxonomy_move_journal WHERE id = ?", (row["id"],))
            await conn.commit()
            result["lost"] += 1
    return result
