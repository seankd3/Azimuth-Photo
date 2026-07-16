"""Shared helpers for repository query batching."""


def chunked(values, chunk_size: int = 500):
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]


async def stage_temp_ids(conn, table_name: str, values) -> None:
    """Stage an arbitrary-size ID scope without crossing SQLite's variable limit."""
    if not table_name.isidentifier():
        raise ValueError(f"Invalid temporary table name: {table_name}")
    await conn.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {table_name} "
        "(image_id INTEGER PRIMARY KEY)"
    )
    await conn.execute(f"DELETE FROM {table_name}")
    ids = list(dict.fromkeys(int(value) for value in values))
    for chunk in chunked(ids, 900):
        placeholders = ",".join("(?)" for _ in chunk)
        await conn.execute(
            f"INSERT INTO {table_name} (image_id) VALUES {placeholders}",
            chunk,
        )


def stage_temp_ids_sync(conn, table_name: str, values) -> None:
    """Synchronous counterpart for repository work running in worker threads."""
    if not table_name.isidentifier():
        raise ValueError(f"Invalid temporary table name: {table_name}")
    conn.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {table_name} "
        "(image_id INTEGER PRIMARY KEY)"
    )
    conn.execute(f"DELETE FROM {table_name}")
    ids = list(dict.fromkeys(int(value) for value in values))
    for chunk in chunked(ids, 900):
        placeholders = ",".join("(?)" for _ in chunk)
        conn.execute(
            f"INSERT INTO {table_name} (image_id) VALUES {placeholders}",
            chunk,
        )
