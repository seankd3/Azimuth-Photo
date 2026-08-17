"""Hierarchical manual keywords and per-image IPTC fields.

Keyword assignments deliberately store only the chosen leaf.  Ancestors are
resolved by the read/query helpers, which keeps a Lightroom taxonomy compact
and makes reparenting safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import asyncio
import sqlite3

from model import decisions, photos, sets
from typing import Any
import xml.etree.ElementTree as etree

import db
from data import connection
import judgements


RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
XMP_RIGHTS_NAMESPACE = "http://ns.adobe.com/xap/1.0/rights/"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_XPACKET_ID = "W5M0MpCehiHzreSzNTczkc9d"
etree.register_namespace("dc", DC_NAMESPACE)
etree.register_namespace("xmpRights", XMP_RIGHTS_NAMESPACE)






SEPARATOR = " > "


def clean_path(path: str) -> str:
    """A keyword path, canonically spaced.

    `>` is the separator the panel types and the one the old tree split on.
    Canonicalising here is what makes "Animals>Cats" and "Animals  >  Cats" the
    same keyword rather than two, now that the name *is* the identity.
    """

    parts = [" ".join(part.split()) for part in str(path or "").split(">")]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("A keyword name is required")
    if any(len(part) > 180 for part in parts):
        raise ValueError("Keyword names must be 180 characters or fewer")
    return SEPARATOR.join(parts)






























IPTC_FIELDS = ("title", "caption", "copyright", "creator")


def _iptc_of(conn, image_id: int) -> dict[str, Any]:
    digest = photos.hashes(conn, [image_id])
    said = decisions.latest(conn, digest[0], decisions_iptc()) if digest else None
    fields = {key: "" for key in IPTC_FIELDS}
    return {"image_id": image_id, **fields, **{k: v for k, v in (said or {}).items() if k in fields}}


def decisions_iptc() -> str:
    """The family. Named here because IPTC is what the owner wrote, not what we read."""

    return "iptc"


async def get_iptc(image_id: int) -> dict[str, Any]:
    """What the owner wrote about this photograph.

    Was a row in `iptc_fields` — a table nothing in the boot path ever created,
    which existed on this machine only because a helper ran `executescript` as a
    side effect of being called. That is the "a table created by hand is a table
    that does not exist" lesson, and the answer is not to add it to the schema:
    a title is something the owner decided, so it lives in the log with the rest
    of what they decided, keyed on the photograph rather than on a row id.
    """

    def job():
        conn = connection.open_sync(db.DB_PATH)
        try:
            return _iptc_of(conn, image_id)
        finally:
            conn.close()

    return await asyncio.to_thread(job)


async def save_iptc(image_id: int, *, title: str = "", caption: str = "",
                    copyright: str = "", creator: str = "") -> dict[str, Any]:
    values = {key: str(value or "").strip() for key, value in {
        "title": title, "caption": caption, "copyright": copyright, "creator": creator,
    }.items()}
    if any(len(value) > 10_000 for value in values.values()):
        raise ValueError("IPTC values must be 10,000 characters or fewer")

    def job():
        conn = connection.open_sync(db.DB_PATH)
        try:
            digest = photos.hashes(conn, [image_id])
            if not digest:
                raise ValueError("That photograph is not identified yet.")
            decisions.decide(conn, digest[0], decisions_iptc(), values)
            conn.commit()
            return _iptc_of(conn, image_id)
        finally:
            conn.close()

    return await asyncio.to_thread(job)


def _sync_keyword(conn: sqlite3.Connection, name: str, parent: str | None) -> str:
    """The set id for one keyword, minted if new. Matched on the whole path.

    `parent_id` became `parent` — a path, not a row — because the hierarchy is
    the name. Lightroom's nested keywords arrive as a chain, so the child's path
    is the parent's path plus its own segment, and no tree is built.
    """

    path = clean_path(f"{parent}{SEPARATOR}{name}" if parent else name)
    for said in sets.all(conn, kind=sets.KEYWORD):
        if said["name"].lower() == path.lower():
            return said["id"]
    return sets.create(conn, path, kind=sets.KEYWORD)


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
        parent_path = resolve(int(parent), visiting | {catalog_keyword_id}) if parent is not None else None
        parent_name = (sets.describe(library, parent_path) or {}).get("name") if parent_path else None
        cache[catalog_keyword_id] = _sync_keyword(library, name, parent_name) if not dry_run else str(catalog_keyword_id)
        return cache[catalog_keyword_id]

    assigned = 0
    for row in rows:
        image_id = matched_catalog_ids.get(int(row["image"]))
        keyword_id = resolve(int(row["id_local"]))
        if image_id is None or keyword_id is None:
            continue
        assigned += 1
        if not dry_run:
            digest = photos.hashes(library, [image_id])
            if digest:
                sets.add(library, keyword_id, digest)
    return assigned


def xmp_metadata_for_image(db_path: str, image_id: int) -> dict[str, Any]:
    """Read direct keywords and editable IPTC fields for an XMP write-back."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Keywords come from the log, not from `image_keywords`. Reading the old
        # table here would have written an empty `dc:subject` into every
        # photograph Lightroom then read back — a silent erasure, which is
        # exactly the failure mode a write-back path cannot afford.
        digest = photos.hashes(conn, [image_id])
        names = []
        if digest:
            names = sorted(
                (sets.describe(conn, i) or {}).get("name", "")
                for i in sets.sets_of(conn, digest[0], kind=sets.KEYWORD)
            )
        iptc = {k: v for k, v in _iptc_of(conn, image_id).items() if k in IPTC_FIELDS}
        return {
            "keywords": [n for n in names if n],
            "iptc": {k: v for k, v in iptc.items() if v},
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
