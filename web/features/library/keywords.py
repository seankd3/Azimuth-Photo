"""Hierarchical manual keywords and per-image IPTC fields.

Keyword assignments deliberately store only the chosen leaf.  Ancestors are
resolved by the read/query helpers, which keeps a Lightroom taxonomy compact
and makes reparenting safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import sqlite3
from typing import Any
import xml.etree.ElementTree as etree

import db


KEYWORD_DDL = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES keywords(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_keywords_parent_name ON keywords(parent_id, name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS image_keywords (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    origin TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (image_id, keyword_id)
);
CREATE INDEX IF NOT EXISTS idx_image_keywords_keyword_image ON image_keywords(keyword_id, image_id);
CREATE TABLE IF NOT EXISTS iptc_fields (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    copyright TEXT NOT NULL DEFAULT '',
    creator TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
XMP_RIGHTS_NAMESPACE = "http://ns.adobe.com/xap/1.0/rights/"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_XPACKET_ID = "W5M0MpCehiHzreSzNTczkc9d"
etree.register_namespace("dc", DC_NAMESPACE)
etree.register_namespace("xmpRights", XMP_RIGHTS_NAMESPACE)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        raise ValueError("A keyword name is required")
    if len(cleaned) > 180:
        raise ValueError("Keyword names must be 180 characters or fewer")
    return cleaned


def _clean_path(path: str | Iterable[str]) -> list[str]:
    values = path.split(">") if isinstance(path, str) else path
    parts = [_clean_name(part) for part in values]
    if not parts:
        raise ValueError("A keyword path is required")
    return parts


async def ensure_schema(conn=None) -> None:
    """Create additive keyword/IPTC tables without a catalogue migration wait."""

    owns_connection = conn is None
    if owns_connection:
        conn = await db.get_db()
    try:
        await conn.executescript(KEYWORD_DDL)
        if owns_connection:
            await conn.commit()
    finally:
        if owns_connection:
            await conn.close()


async def _keyword_row(conn, keyword_id: int) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "SELECT id, name, parent_id, created_at FROM keywords WHERE id = ?", (keyword_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_keywords(*, query: str = "") -> list[dict[str, Any]]:
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        needle = "%" + str(query or "").strip() + "%"
        cursor = await conn.execute(
            """
            WITH RECURSIVE tree(id, name, parent_id, created_at, path, depth) AS (
                SELECT id, name, parent_id, created_at, name, 0 FROM keywords WHERE parent_id IS NULL
                UNION ALL
                SELECT child.id, child.name, child.parent_id, child.created_at,
                       tree.path || ' > ' || child.name, tree.depth + 1
                FROM keywords child JOIN tree ON child.parent_id = tree.id
            )
            SELECT tree.*, COUNT(image_keywords.image_id) AS direct_count
            FROM tree LEFT JOIN image_keywords ON image_keywords.keyword_id = tree.id
            WHERE ? = '%%' OR tree.name LIKE ? COLLATE NOCASE OR tree.path LIKE ? COLLATE NOCASE
            GROUP BY tree.id ORDER BY tree.path COLLATE NOCASE
            """,
            (needle, needle, needle),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await conn.close()


async def create_keyword(name: str, parent_id: int | None = None) -> dict[str, Any]:
    clean = _clean_name(name)
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        if parent_id is not None and not await _keyword_row(conn, int(parent_id)):
            raise LookupError("Parent keyword was not found")
        cursor = await conn.execute(
            "SELECT id FROM keywords WHERE parent_id IS ? AND lower(name) = lower(?)", (parent_id, clean)
        )
        existing = await cursor.fetchone()
        if existing:
            return (await _keyword_row(conn, int(existing["id"]))) or {}
        cursor = await conn.execute(
            "INSERT INTO keywords (name, parent_id, created_at) VALUES (?, ?, ?)",
            (clean, parent_id, _now()),
        )
        await conn.commit()
        return (await _keyword_row(conn, int(cursor.lastrowid))) or {}
    finally:
        await conn.close()


async def resolve_keyword_path(path: str | Iterable[str], *, create: bool = True) -> dict[str, Any] | None:
    parts = _clean_path(path)
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        parent_id: int | None = None
        current = None
        for part in parts:
            cursor = await conn.execute(
                "SELECT id, name, parent_id, created_at FROM keywords "
                "WHERE parent_id IS ? AND lower(name) = lower(?)",
                (parent_id, part),
            )
            row = await cursor.fetchone()
            if row is None:
                if not create:
                    return None
                cursor = await conn.execute(
                    "INSERT INTO keywords (name, parent_id, created_at) VALUES (?, ?, ?)",
                    (part, parent_id, _now()),
                )
                current = await _keyword_row(conn, int(cursor.lastrowid))
            else:
                current = dict(row)
            parent_id = int(current["id"])
        await conn.commit()
        return current
    finally:
        await conn.close()


async def update_keyword(keyword_id: int, *, name: str | None = None, parent_id: int | None = None, move: bool = False) -> dict[str, Any]:
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        existing = await _keyword_row(conn, keyword_id)
        if not existing:
            raise LookupError("Keyword was not found")
        target_name = _clean_name(name) if name is not None else existing["name"]
        target_parent = parent_id if move else existing["parent_id"]
        if target_parent is not None:
            if int(target_parent) == keyword_id:
                raise ValueError("A keyword cannot parent itself")
            cursor = await conn.execute(
                "WITH RECURSIVE descendants(id) AS (SELECT id FROM keywords WHERE parent_id = ? "
                "UNION ALL SELECT child.id FROM keywords child JOIN descendants ON child.parent_id = descendants.id) "
                "SELECT 1 FROM descendants WHERE id = ?",
                (keyword_id, target_parent),
            )
            if await cursor.fetchone():
                raise ValueError("A keyword cannot move beneath its descendant")
            if not await _keyword_row(conn, int(target_parent)):
                raise LookupError("Parent keyword was not found")
        duplicate = await conn.execute(
            "SELECT id FROM keywords WHERE parent_id IS ? AND lower(name) = lower(?) AND id != ?",
            (target_parent, target_name, keyword_id),
        )
        if await duplicate.fetchone():
            raise ValueError("A sibling already has that name")
        await conn.execute(
            "UPDATE keywords SET name = ?, parent_id = ? WHERE id = ?",
            (target_name, target_parent, keyword_id),
        )
        await conn.commit()
        return (await _keyword_row(conn, keyword_id)) or {}
    finally:
        await conn.close()


async def delete_keyword(keyword_id: int) -> None:
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        row = await _keyword_row(conn, keyword_id)
        if not row:
            raise LookupError("Keyword was not found")
        cursor = await conn.execute("SELECT 1 FROM keywords WHERE parent_id = ? LIMIT 1", (keyword_id,))
        if await cursor.fetchone():
            raise ValueError("Move or remove child keywords first")
        await conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        await conn.commit()
    finally:
        await conn.close()


async def assign_keyword(image_ids: Iterable[int], keyword_id: int, *, origin: str = "user") -> int:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    if not ids:
        return 0
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        if not await _keyword_row(conn, keyword_id):
            raise LookupError("Keyword was not found")
        cursor = await conn.executemany(
            "INSERT INTO image_keywords (image_id, keyword_id, origin) VALUES (?, ?, ?) "
            "ON CONFLICT(image_id, keyword_id) DO UPDATE SET origin = excluded.origin",
            [(image_id, keyword_id, origin) for image_id in ids],
        )
        await conn.commit()
        return max(int(cursor.rowcount or 0), 0)
    finally:
        await conn.close()


async def unassign_keyword(image_ids: Iterable[int], keyword_id: int) -> int:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    if not ids:
        return 0
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        placeholders = ",".join("?" for _ in ids)
        cursor = await conn.execute(
            f"DELETE FROM image_keywords WHERE keyword_id = ? AND image_id IN ({placeholders})",
            (keyword_id, *ids),
        )
        await conn.commit()
        return max(int(cursor.rowcount or 0), 0)
    finally:
        await conn.close()


async def image_keywords(image_id: int, *, include_ancestors: bool = True) -> list[dict[str, Any]]:
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        if include_ancestors:
            sql = """
            WITH RECURSIVE tree(id, name, parent_id, path) AS (
                SELECT id, name, parent_id, name FROM keywords WHERE parent_id IS NULL
                UNION
                SELECT child.id, child.name, child.parent_id, tree.path || ' > ' || child.name
                FROM keywords child JOIN tree ON child.parent_id = tree.id
            ), expanded(id, direct, origin) AS (
                SELECT keyword.id, 1, image_keywords.origin
                FROM image_keywords JOIN keywords keyword ON keyword.id = image_keywords.keyword_id
                WHERE image_keywords.image_id = ?
                UNION
                SELECT keyword.parent_id, 0, expanded.origin
                FROM keywords keyword JOIN expanded ON keyword.id = expanded.id
                WHERE keyword.parent_id IS NOT NULL
            )
            SELECT id, name, parent_id, MAX(direct) AS direct, GROUP_CONCAT(DISTINCT origin) AS origin,
                   path
            FROM tree JOIN expanded USING(id) GROUP BY id ORDER BY path COLLATE NOCASE
            """
        else:
            sql = """
            WITH RECURSIVE tree(id, name, parent_id, path) AS (
                SELECT id, name, parent_id, name FROM keywords WHERE parent_id IS NULL
                UNION ALL SELECT child.id, child.name, child.parent_id, tree.path || ' > ' || child.name
                FROM keywords child JOIN tree ON child.parent_id = tree.id
            )
            SELECT tree.id, tree.name, tree.parent_id, 1 AS direct, image_keywords.origin, tree.path
            FROM image_keywords JOIN tree ON tree.id = image_keywords.keyword_id
            WHERE image_keywords.image_id = ? ORDER BY tree.path COLLATE NOCASE
            """
        cursor = await conn.execute(sql, (image_id,))
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await conn.close()


async def keyword_image_ids(keyword_id: int) -> list[int]:
    """Images assigned to this keyword or any descendant, without copied rows."""

    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        cursor = await conn.execute(
            """
            WITH RECURSIVE subtree(id) AS (
                SELECT id FROM keywords WHERE id = ?
                UNION ALL
                SELECT child.id FROM keywords child JOIN subtree ON child.parent_id = subtree.id
            )
            SELECT DISTINCT image_id FROM image_keywords
            WHERE keyword_id IN (SELECT id FROM subtree) ORDER BY image_id
            """,
            (keyword_id,),
        )
        return [int(row["image_id"]) for row in await cursor.fetchall()]
    finally:
        await conn.close()


async def get_iptc(image_id: int) -> dict[str, Any]:
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        cursor = await conn.execute(
            "SELECT title, caption, copyright, creator, updated_at FROM iptc_fields WHERE image_id = ?", (image_id,)
        )
        row = await cursor.fetchone()
        fields = dict(row) if row else {"title": "", "caption": "", "copyright": "", "creator": "", "updated_at": None}
        return {"image_id": image_id, **fields}
    finally:
        await conn.close()


async def save_iptc(image_id: int, *, title: str = "", caption: str = "", copyright: str = "", creator: str = "") -> dict[str, Any]:
    values = {key: str(value or "").strip() for key, value in {
        "title": title, "caption": caption, "copyright": copyright, "creator": creator,
    }.items()}
    if any(len(value) > 10_000 for value in values.values()):
        raise ValueError("IPTC values must be 10,000 characters or fewer")
    conn = await db.get_db()
    try:
        await ensure_schema(conn)
        await conn.execute(
            "INSERT INTO iptc_fields (image_id, title, caption, copyright, creator, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(image_id) DO UPDATE SET "
            "title=excluded.title, caption=excluded.caption, copyright=excluded.copyright, "
            "creator=excluded.creator, updated_at=excluded.updated_at",
            (image_id, values["title"], values["caption"], values["copyright"], values["creator"], _now()),
        )
        await conn.commit()
        return await get_iptc(image_id)
    finally:
        await conn.close()


def _sync_keyword(conn: sqlite3.Connection, name: str, parent_id: int | None) -> int:
    clean = _clean_name(name)
    row = conn.execute(
        "SELECT id FROM keywords WHERE parent_id IS ? AND lower(name) = lower(?)", (parent_id, clean)
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO keywords (name, parent_id, created_at) VALUES (?, ?, ?)",
        (clean, parent_id, _now()),
    )
    return int(cursor.lastrowid)


def import_lightroom_keywords(
    library: sqlite3.Connection,
    catalog: sqlite3.Connection,
    matched_catalog_ids: dict[int, int],
    *,
    dry_run: bool = False,
) -> int:
    """Import Lightroom's keyword tree using only matched local images.

    The synchronous function intentionally receives both connections: the
    catalog copy is read-only and ``library`` is already inside lrcat's
    transaction, so import remains atomic with its rating/pick writes.
    """

    if not matched_catalog_ids:
        return 0
    tables = {row[0] for row in catalog.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"AgLibraryKeyword", "AgLibraryKeywordImage"}.issubset(tables):
        return 0
    columns = {row[1] for row in catalog.execute("PRAGMA table_info(AgLibraryKeyword)")}
    parent_column = "parent" if "parent" in columns else None
    select_parent = f", {parent_column} AS parent" if parent_column else ", NULL AS parent"
    rows = catalog.execute(
        "SELECT keyword.id_local, keyword.name" + select_parent + ", keyword_image.image "
        "FROM AgLibraryKeywordImage keyword_image "
        "JOIN AgLibraryKeyword keyword ON keyword.id_local = keyword_image.tag"
    ).fetchall()
    if not rows:
        return 0
    definitions = {
        int(row["id_local"]): (str(row["name"] or ""), row["parent"])
        for row in catalog.execute("SELECT id_local, name" + select_parent + " FROM AgLibraryKeyword")
    }
    library.executescript(KEYWORD_DDL)
    cache: dict[int, int | None] = {}

    def resolve(catalog_keyword_id: int, visiting: set[int] | None = None) -> int | None:
        if catalog_keyword_id in cache:
            return cache[catalog_keyword_id]
        name, parent = definitions.get(catalog_keyword_id, ("", None))
        if not str(name).strip():
            cache[catalog_keyword_id] = None
            return None
        visiting = visiting or set()
        if catalog_keyword_id in visiting:
            cache[catalog_keyword_id] = None
            return None
        parent_id = resolve(int(parent), visiting | {catalog_keyword_id}) if parent is not None else None
        cache[catalog_keyword_id] = _sync_keyword(library, name, parent_id) if not dry_run else catalog_keyword_id
        return cache[catalog_keyword_id]

    assigned = 0
    for row in rows:
        image_id = matched_catalog_ids.get(int(row["image"]))
        keyword_id = resolve(int(row["id_local"]))
        if image_id is None or keyword_id is None:
            continue
        assigned += 1
        if not dry_run:
            library.execute(
                "INSERT INTO image_keywords (image_id, keyword_id, origin) VALUES (?, ?, 'lightroom') "
                "ON CONFLICT(image_id, keyword_id) DO UPDATE SET origin = 'lightroom'",
                (image_id, keyword_id),
            )
    return assigned


def xmp_metadata_for_image(db_path: str, image_id: int) -> dict[str, Any]:
    """Read direct keywords and editable IPTC fields for an XMP write-back."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(KEYWORD_DDL)
        keyword_rows = conn.execute(
            "SELECT keyword.name FROM image_keywords JOIN keywords keyword ON keyword.id = image_keywords.keyword_id "
            "WHERE image_keywords.image_id = ? ORDER BY keyword.name COLLATE NOCASE",
            (image_id,),
        ).fetchall()
        iptc = conn.execute(
            "SELECT title, caption, copyright, creator FROM iptc_fields WHERE image_id = ?", (image_id,)
        ).fetchone()
        return {
            "keywords": [str(row["name"]) for row in keyword_rows],
            "iptc": dict(iptc) if iptc else {},
        }
    finally:
        conn.close()


def decorate_xmp_packet(packet: str, metadata: dict[str, Any]) -> str:
    """Add Lightroom-readable Dublin Core/IPTC values to an XMP packet."""

    start = packet.find("<x:xmpmeta")
    end = packet.rfind("</x:xmpmeta>")
    if start < 0 or end < start:
        return packet
    root = etree.fromstring(packet[start:end + len("</x:xmpmeta>")])
    description = next((node for node in root.iter() if node.tag == f"{{{RDF_NAMESPACE}}}Description"), None)
    if description is None:
        return packet
    for namespace, local in ((DC_NAMESPACE, "subject"), (DC_NAMESPACE, "title"), (DC_NAMESPACE, "description"), (DC_NAMESPACE, "creator"), (DC_NAMESPACE, "rights"), (XMP_RIGHTS_NAMESPACE, "Marked")):
        for child in list(description):
            if child.tag == f"{{{namespace}}}{local}":
                description.remove(child)
    iptc = metadata.get("iptc") or {}
    keywords = [str(value).strip() for value in metadata.get("keywords") or [] if str(value).strip()]
    if keywords:
        subject = etree.SubElement(description, f"{{{DC_NAMESPACE}}}subject")
        bag = etree.SubElement(subject, f"{{{RDF_NAMESPACE}}}Bag")
        for keyword in keywords:
            etree.SubElement(bag, f"{{{RDF_NAMESPACE}}}li").text = keyword
    for local, value in (("title", iptc.get("title")), ("description", iptc.get("caption")), ("rights", iptc.get("copyright"))):
        if not value:
            continue
        field = etree.SubElement(description, f"{{{DC_NAMESPACE}}}{local}")
        alternative = etree.SubElement(field, f"{{{RDF_NAMESPACE}}}Alt")
        item = etree.SubElement(alternative, f"{{{RDF_NAMESPACE}}}li")
        item.set(f"{{{XML_NAMESPACE}}}lang", "x-default")
        item.text = str(value)
    if iptc.get("creator"):
        creator = etree.SubElement(description, f"{{{DC_NAMESPACE}}}creator")
        sequence = etree.SubElement(creator, f"{{{RDF_NAMESPACE}}}Seq")
        etree.SubElement(sequence, f"{{{RDF_NAMESPACE}}}li").text = str(iptc["creator"])
    etree.indent(root, space="  ")
    xml = etree.tostring(root, encoding="unicode", short_empty_elements=True)
    return f'<?xpacket begin="" id="{_XPACKET_ID}"?>\n{xml}\n<?xpacket end="w"?>'
