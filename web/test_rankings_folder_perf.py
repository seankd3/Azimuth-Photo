import sqlite3

from data.repositories import rankings


ACTIVE_PREDICATE = (
    "i.status IN ('kept', 'maybe') AND i.missing_at IS NULL AND i.vc_of IS NULL"
)


def _fixture_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE images ("
        "id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, elo REAL, "
        "status TEXT, missing_at REAL, vc_of INTEGER)"
    )
    conn.execute(
        "CREATE INDEX idx_images_active_filepath_elo "
        "ON images(filepath, elo DESC, id) "
        "WHERE status IN ('kept','maybe') AND missing_at IS NULL AND vc_of IS NULL"
    )
    rows = [
        (1, "/archive/RAWS/2024/a.cr3", 1500, "kept", None, None),
        (2, "/archive/RAWS/2024/trip/b.cr3", 1400, "maybe", None, None),
        (3, "/archive/RAWS/2023/c.cr3", 1300, "kept", None, None),
        (4, "/archive/100%_real/d.cr3", 1200, "kept", None, None),
        (5, "/archive/100xxreal/e.cr3", 1100, "kept", None, None),
        (6, "/archive/RAWS/2024/missing.cr3", 1000, "kept", 1, None),
        (7, "/archive/RAWS/2024/virtual.cr3", 900, "kept", None, 1),
        (8, "/archive/RAWS/2024/rejected.cr3", 800, "rejected", None, None),
    ]
    conn.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?)", rows)
    return conn


def _ids_for(conn: sqlite3.Connection, condition: str, params: list) -> list[int]:
    rows = conn.execute(
        f"SELECT i.id FROM images i WHERE {ACTIVE_PREDICATE} AND {condition} ORDER BY i.id",
        params,
    ).fetchall()
    return [row[0] for row in rows]


def _old_like_filter(folder) -> tuple[str, list[str]]:
    parts = []
    params = []
    for value in rankings.normalized_folder_values(folder):
        parts.append("i.filepath LIKE ? ESCAPE '\\'")
        if value.startswith("/"):
            params.append(f"{rankings.escape_like(value)}/%")
        else:
            params.append(f"%/{rankings.escape_like(value)}/%")
    if len(parts) == 1:
        return parts[0], params
    return f"({' OR '.join(parts)})", params


def test_absolute_folder_ranges_match_escaped_like_row_sets():
    conn = _fixture_connection()
    try:
        for folder in (
            "/archive/RAWS/2024/",
            "/archive/100%_real",
            ["/archive/RAWS/2023", "/archive/100%_real"],
        ):
            range_condition, range_params = rankings.folder_filter_sql(folder)
            like_condition, like_params = _old_like_filter(folder)
            assert _ids_for(conn, range_condition, range_params) == _ids_for(
                conn, like_condition, like_params
            )
    finally:
        conn.close()


def test_folder_elo_query_uses_covering_filepath_index():
    conn = _fixture_connection()
    try:
        condition, params = rankings.folder_filter_sql("/archive/RAWS/2024")
        image_source = rankings.ranking_image_source(
            "elo",
            folder="/archive/RAWS/2024",
            id_filter=None,
            text_query="",
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT i.id, i.elo "
            f"FROM {image_source} WHERE {ACTIVE_PREDICATE} AND {condition} "
            "ORDER BY i.elo DESC LIMIT 100",
            params,
        ).fetchall()
        details = "\n".join(row[3] for row in plan)
        assert "idx_images_active_filepath_elo" in details
        assert "filepath>? AND filepath<?" in details
    finally:
        conn.close()
