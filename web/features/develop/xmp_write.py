"""Adobe Camera Raw XMP serialization and conservative write-back.

Sidecars are the default write target.  Embedded writes are intentionally
limited to fixed-size DNG packet splices: the original packet is backed up and
the DNG is never rebuilt or allowed to change size.
"""

from __future__ import annotations

import json
import math
import mmap
import os
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as etree
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data import connection


XMP_NAMESPACE = "adobe:ns:meta/"
RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CRS_NAMESPACE = "http://ns.adobe.com/camera-raw-settings/1.0/"
_XPACKET_ID = "W5M0MpCehiHzreSzNTczkc9d"
_RAW_EXTENSIONS = frozenset({".dng", ".cr2", ".cr3"})
_XML_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_XMP_START = b"<x:xmpmeta"
_XMP_END = b"</x:xmpmeta>"
_XPACKET_START = b"<?xpacket begin="
_XPACKET_END = b"<?xpacket end="

etree.register_namespace("x", XMP_NAMESPACE)
etree.register_namespace("rdf", RDF_NAMESPACE)
etree.register_namespace("crs", CRS_NAMESPACE)


def _qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _split_name(name: str) -> tuple[str, str]:
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        return namespace, local
    return "", name.split(":", 1)[-1]


def _is_crs_name(name: str) -> bool:
    namespace, _local = _split_name(name)
    return namespace == CRS_NAMESPACE or name.startswith("crs:")


def _normalize_value(value: object) -> bool | int | float | str:
    clean = str(value or "").strip()
    if clean.lower() == "true":
        return True
    if clean.lower() == "false":
        return False
    if not _NUMBER.fullmatch(clean):
        return clean
    numeric = float(clean)
    return int(numeric) if numeric.is_integer() else numeric


def adobe_value(value: object) -> str:
    """Format a canonical scalar without losing numeric round-trip fidelity."""

    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("XMP numbers must be finite")
        if value.is_integer():
            return str(int(value))
        rendered = repr(value)
        if "e" not in rendered.lower():
            whole, dot, fraction = rendered.partition(".")
            if dot and len(fraction) == 1:
                rendered = f"{whole}.{fraction}0"
        return f"+{rendered}" if value > 0 else rendered
    if value is None:
        raise TypeError("XMP settings cannot contain null scalar values")
    return str(value)


def _valid_key(key: object) -> str:
    local = str(key)
    if not _XML_KEY.fullmatch(local):
        raise ValueError(f"invalid Camera Raw setting name: {local!r}")
    return local


def _is_scalar(value: object) -> bool:
    return not isinstance(value, (Mapping, list, tuple))


def _append_resource(parent: etree.Element, values: Mapping[str, object]) -> None:
    for raw_key, value in values.items():
        key = _valid_key(raw_key)
        if _is_scalar(value):
            parent.set(_qname(CRS_NAMESPACE, key), adobe_value(value))
        else:
            _append_property(parent, key, value)


def _append_sequence(parent: etree.Element, key: str, values: Sequence[object]) -> None:
    property_element = etree.SubElement(parent, _qname(CRS_NAMESPACE, key))
    sequence = etree.SubElement(property_element, _qname(RDF_NAMESPACE, "Seq"))
    for value in values:
        item = etree.SubElement(sequence, _qname(RDF_NAMESPACE, "li"))
        if isinstance(value, Mapping):
            # Lightroom wraps correction groups in rdf:Description, while
            # masks inside CorrectionMasks carry their crs attributes on li.
            resource = item
            if key == "MaskGroupBasedCorrections":
                resource = etree.SubElement(item, _qname(RDF_NAMESPACE, "Description"))
            _append_resource(resource, value)
        elif isinstance(value, (list, tuple)):
            nested = etree.SubElement(item, _qname(RDF_NAMESPACE, "Seq"))
            for nested_value in value:
                nested_item = etree.SubElement(nested, _qname(RDF_NAMESPACE, "li"))
                nested_item.text = adobe_value(nested_value)
        else:
            item.text = adobe_value(value)


def _append_property(parent: etree.Element, key: str, value: object) -> None:
    if isinstance(value, Mapping):
        property_element = etree.SubElement(parent, _qname(CRS_NAMESPACE, key))
        resource = etree.SubElement(property_element, _qname(RDF_NAMESPACE, "Description"))
        _append_resource(resource, value)
        return
    if isinstance(value, (list, tuple)):
        _append_sequence(parent, key, value)
        return
    property_element = etree.SubElement(parent, _qname(CRS_NAMESPACE, key))
    property_element.text = adobe_value(value)


def serialize(settings: Mapping[str, object]) -> str:
    """Serialize canonical Develop settings into a deterministic XMP packet."""

    if not isinstance(settings, Mapping):
        raise TypeError("settings must be an object")
    root = etree.Element(_qname(XMP_NAMESPACE, "xmpmeta"))
    rdf = etree.SubElement(root, _qname(RDF_NAMESPACE, "RDF"))
    description = etree.SubElement(rdf, _qname(RDF_NAMESPACE, "Description"))
    description.set(_qname(RDF_NAMESPACE, "about"), "")
    _append_resource(description, settings)
    etree.indent(root, space="  ")
    xml = etree.tostring(root, encoding="unicode", short_empty_elements=True)
    return (
        f'<?xpacket begin="" id="{_XPACKET_ID}"?>\n'
        f"{xml}\n"
        '<?xpacket end="w"?>'
    )


def _rdf_sequence(element: etree.Element) -> etree.Element | None:
    return next(
        (child for child in element if child.tag == _qname(RDF_NAMESPACE, "Seq")),
        None,
    )


def _rdf_description(element: etree.Element) -> etree.Element | None:
    return next(
        (child for child in element if child.tag == _qname(RDF_NAMESPACE, "Description")),
        None,
    )


def _parse_resource(element: etree.Element) -> dict[str, Any]:
    result = {
        _split_name(name)[1]: _normalize_value(value)
        for name, value in element.attrib.items()
        if _is_crs_name(name)
    }
    for child in element:
        if not _is_crs_name(child.tag):
            continue
        _namespace, key = _split_name(child.tag)
        result[key] = _parse_property(child)
    return result


def _parse_item(item: etree.Element) -> Any:
    description = _rdf_description(item)
    if description is not None:
        return _parse_resource(description)
    if any(_is_crs_name(name) for name in item.attrib) or any(
        _is_crs_name(child.tag) for child in item
    ):
        return _parse_resource(item)
    nested = _rdf_sequence(item)
    if nested is not None:
        return [_parse_item(child) for child in nested if child.tag == _qname(RDF_NAMESPACE, "li")]
    return _normalize_value("".join(item.itertext()).strip())


def _parse_property(element: etree.Element) -> Any:
    sequence = _rdf_sequence(element)
    if sequence is not None:
        return [
            _parse_item(item)
            for item in sequence
            if item.tag == _qname(RDF_NAMESPACE, "li")
        ]
    description = _rdf_description(element)
    if description is not None:
        return _parse_resource(description)
    if any(_is_crs_name(name) for name in element.attrib):
        return _parse_resource(element)
    return _normalize_value("".join(element.itertext()).strip())


def parse_xmp_text(payload: str | bytes) -> dict[str, Any]:
    """Parse scalar and nested CRS resources emitted by Lightroom or us."""

    root = etree.fromstring(payload)
    description = next(
        (element for element in root.iter() if element.tag == _qname(RDF_NAMESPACE, "Description")),
        None,
    )
    return _parse_resource(description) if description is not None else {}


def sidecar_path(raw_path: str | os.PathLike[str]) -> Path:
    return Path(raw_path).with_suffix(".xmp")


def backup_path(raw_path: str | os.PathLike[str]) -> Path:
    return Path(f"{raw_path}.xmp-backup")


def _atomic_write(path: Path, payload: bytes) -> bool:
    if path.is_file() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, old_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def write_sidecar(
    raw_path: str, settings: Mapping[str, object], *, metadata: Mapping[str, object] | None = None
) -> dict[str, Any]:
    target = sidecar_path(raw_path)
    packet = serialize(settings)
    if metadata and (metadata.get("keywords") or metadata.get("iptc")):
        from features.library import keywords as _keywords

        packet = _keywords.decorate_xmp_packet(packet, dict(metadata))
    changed = _atomic_write(target, packet.encode("utf-8"))
    return {
        "mode": "sidecar",
        "status": "written" if changed else "unchanged",
        "target": str(target),
    }


@dataclass(frozen=True)
class EmbeddedPacket:
    start: int
    end: int
    payload: bytes
    envelope: bool

    @property
    def capacity(self) -> int:
        return self.end - self.start


def _embedded_packet(path: str) -> EmbeddedPacket | None:
    with open(path, "rb") as handle:
        with mmap.mmap(handle.fileno(), length=0, access=mmap.ACCESS_READ) as data:
            xmp_start = data.find(_XMP_START)
            if xmp_start < 0:
                return None
            xmp_close = data.find(_XMP_END, xmp_start)
            if xmp_close < 0:
                return None
            xmp_end = xmp_close + len(_XMP_END)
            packet_start = data.rfind(_XPACKET_START, max(0, xmp_start - 4096), xmp_start)
            packet_end_start = data.find(_XPACKET_END, xmp_end, min(len(data), xmp_end + (1 << 20)))
            if packet_start >= 0 and packet_end_start >= 0:
                packet_end = data.find(b"?>", packet_end_start, packet_end_start + 128)
                if packet_end >= 0:
                    end = packet_end + 2
                    return EmbeddedPacket(packet_start, end, bytes(data[packet_start:end]), True)
            return EmbeddedPacket(xmp_start, xmp_end, bytes(data[xmp_start:xmp_end]), False)


def _without_xpacket(packet: bytes) -> bytes:
    start = packet.find(_XMP_START)
    end = packet.rfind(_XMP_END)
    return packet[start : end + len(_XMP_END)] if start >= 0 and end >= 0 else packet


def _padded_packet(packet: bytes, capacity: int, *, envelope: bool) -> bytes | None:
    candidate = packet if envelope else _without_xpacket(packet)
    if len(candidate) > capacity:
        return None
    padding = capacity - len(candidate)
    if envelope:
        end_instruction = candidate.rfind(_XPACKET_END)
        if end_instruction >= 0:
            return candidate[:end_instruction] + (b" " * padding) + candidate[end_instruction:]
    return candidate + (b" " * padding)


def _write_backup_once(path: Path, payload: bytes) -> bool:
    try:
        with open(path, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except FileExistsError:
        return False


def _splice(path: str, region: EmbeddedPacket, payload: bytes) -> None:
    with open(path, "r+b") as handle:
        handle.seek(region.start)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def write_embedded_dng(raw_path: str, settings: Mapping[str, object]) -> dict[str, Any]:
    """Splice a DNG packet if safe, otherwise produce the sidecar fallback."""

    if Path(raw_path).suffix.lower() != ".dng":
        fallback = write_sidecar(raw_path, settings)
        return {
            **fallback,
            "status": "fallback_sidecar" if fallback["status"] == "written" else "fallback_unchanged",
            "requested_mode": "embedded",
            "note": "Embedded XMP is supported only for DNG; wrote the safe sidecar target.",
        }
    region = _embedded_packet(raw_path)
    if region is None:
        fallback = write_sidecar(raw_path, settings)
        return {
            **fallback,
            "status": "fallback_sidecar" if fallback["status"] == "written" else "fallback_unchanged",
            "requested_mode": "embedded",
            "note": "The DNG has no discoverable XMP packet; wrote a sidecar without changing the DNG.",
        }
    serialized = serialize(settings).encode("utf-8")
    replacement = _padded_packet(serialized, region.capacity, envelope=region.envelope)
    if replacement is None:
        fallback = write_sidecar(raw_path, settings)
        return {
            **fallback,
            "status": "fallback_sidecar" if fallback["status"] == "written" else "fallback_unchanged",
            "requested_mode": "embedded",
            "packet_capacity": region.capacity,
            "packet_required": len(serialized if region.envelope else _without_xpacket(serialized)),
            "note": "The new XMP packet does not fit the existing DNG packet; wrote a sidecar and left the DNG unchanged.",
        }
    if replacement == region.payload:
        return {
            "mode": "embedded",
            "status": "unchanged",
            "target": raw_path,
            "backup": str(backup_path(raw_path)),
        }
    backup = backup_path(raw_path)
    _write_backup_once(backup, region.payload)
    _splice(raw_path, region, replacement)
    return {
        "mode": "embedded",
        "status": "written",
        "target": raw_path,
        "backup": str(backup),
        "packet_capacity": region.capacity,
        "packet_bytes": len(replacement),
    }


def _settings_row(db_path: str, image_id: int) -> sqlite3.Row | None:
    conn = connection.open_sync(db_path)
    try:
        return conn.execute(
            "SELECT i.id, i.filepath, i.vc_of, i.hub_remote, ds.settings, ds.origin "
            "FROM images i LEFT JOIN develop_settings ds ON ds.image_id = i.id WHERE i.id = ?",
            (image_id,),
        ).fetchone()
    finally:
        connection.close_sync(conn, db_path=db_path)


def write_image_xmp(db_path: str, image_id: int, *, mode: str = "sidecar") -> dict[str, Any]:
    if mode not in {"sidecar", "embedded"}:
        raise ValueError("mode must be 'sidecar' or 'embedded'")
    row = _settings_row(db_path, image_id)
    if row is None:
        return {"image_id": image_id, "status": "not_found", "note": "Image not found."}
    if row["vc_of"] is not None:
        return {
            "image_id": image_id,
            "status": "skipped",
            "origin": str(row["origin"] or "none"),
            "note": "Virtual copies do not own the sidecar - write from the master.",
        }
    if int(row["hub_remote"] or 0) == 1:
        return {
            "image_id": image_id,
            "status": "hub_remote",
            "origin": str(row["origin"] or "none"),
            "note": "This photo is mirrored from the hub; write XMP on the hub.",
        }
    if row["origin"] != "user":
        origin = str(row["origin"] or "none")
        return {
            "image_id": image_id,
            "status": "skipped",
            "origin": origin,
            "note": "Only user-edited settings are written; imported-unmodified settings were left untouched.",
        }
    filepath = str(row["filepath"] or "")
    if Path(filepath).suffix.lower() not in _RAW_EXTENSIONS:
        return {
            "image_id": image_id,
            "status": "skipped",
            "origin": "user",
            "note": "XMP write-back currently targets RAW files only.",
        }
    if not os.path.isfile(filepath):
        return {
            "image_id": image_id,
            "status": "unavailable",
            "origin": "user",
            "note": "The RAW source file is unavailable; nothing was written.",
        }
    try:
        settings = json.loads(row["settings"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        settings = None
    if not isinstance(settings, dict):
        return {
            "image_id": image_id,
            "status": "error",
            "origin": "user",
            "note": "Stored Develop settings are not a JSON object; nothing was written.",
        }
    metadata = None
    if mode == "sidecar":
        try:
            from features.library import keywords as _keywords

            metadata = _keywords.xmp_metadata_for_image(db_path, image_id)
        except Exception:
            metadata = None
    result = (
        write_sidecar(filepath, settings, metadata=metadata)
        if mode == "sidecar"
        else write_embedded_dng(filepath, settings)
    )
    return {"image_id": image_id, "origin": "user", **result}


def write_batch_xmp(db_path: str, image_ids: Sequence[int]) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    results = [write_image_xmp(db_path, image_id, mode="sidecar") for image_id in unique_ids]
    counted = [result for result in results if result["status"] != "hub_remote"]
    return {
        "mode": "sidecar",
        "requested": len(image_ids),
        "processed": len(counted),
        "excluded_hub_remote": len(results) - len(counted),
        "written": sum(result["status"] == "written" for result in counted),
        "unchanged": sum(result["status"] == "unchanged" for result in counted),
        "skipped": sum(result["status"] not in {"written", "unchanged"} for result in counted),
        "results": results,
    }
