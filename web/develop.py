"""Develop: Lightroom's own settings, as decisions.

An edit is one decision (family ``develop``) whose value is the photograph's
crs settings — the exact keys and spellings Lightroom writes into a sidecar,
so import and export are transcription, never translation. The newest
authoritative decision is the photograph's current edit; history and undo are
the log, the same as stars and rotation.

The renderable part of the edit is projected to ``images.develop`` as a
canonical JSON fragment — the crs subset the renderer reads, typed and
sorted — which is what lets a rendition's cache recipe be built per
photograph *in SQL* — the tile of an edited photograph is a different
recipe because it is different pixels, and the tile of an untouched one
keeps the exact recipe it always had.

Sidecars are read on the sweep's rhythm: the walk already sees every ``.xmp``
beside a photograph, and a sidecar whose settings differ from the last
file-authored decision appends a new one. A sidecar is the ``file`` author,
like every fact read from one, and the owner's own in-app answer outranks
it — the same one rule the whole decision log lives by.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

from model import decisions

FAMILY = decisions.DEVELOP
BY_FILE = decisions.FILE

# The crop rectangle, in Lightroom's spelling: unit coordinates of the
# oriented image. CropAngle rides along untouched; every one of the 1,138
# angle values in the real library is exactly 0, so rendering it waits for
# a measured fixture rather than a guessed dialect.
CROP_KEYS = ("CropLeft", "CropTop", "CropRight", "CropBottom")

# Everything the renderer reads, in Lightroom's spelling — the census of the
# real library's own sidecars (docs/DEVELOP.md), minus what is parked with a
# receipt: CropAngle (all zeros), lens and perspective (fixtures first),
# PointColors and the SDR/HDR pair (the parity phase owns them). A key
# outside this set still lives in the decision and round-trips through
# write-back; it just does not change pixels yet.
RENDERED = frozenset((
    *CROP_KEYS,
    "Temperature", "Tint",
    "Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012",
    "Whites2012", "Blacks2012",
    "Texture", "Clarity2012", "Dehaze", "Vibrance", "Saturation",
    "ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue",
    "ParametricDarks", "ParametricLights", "ParametricShadows", "ParametricHighlights",
    "ParametricShadowSplit", "ParametricMidtoneSplit", "ParametricHighlightSplit",
    "HueAdjustmentRed", "HueAdjustmentOrange", "HueAdjustmentYellow", "HueAdjustmentGreen",
    "HueAdjustmentAqua", "HueAdjustmentBlue", "HueAdjustmentPurple", "HueAdjustmentMagenta",
    "SaturationAdjustmentRed", "SaturationAdjustmentOrange", "SaturationAdjustmentYellow",
    "SaturationAdjustmentGreen", "SaturationAdjustmentAqua", "SaturationAdjustmentBlue",
    "SaturationAdjustmentPurple", "SaturationAdjustmentMagenta",
    "LuminanceAdjustmentRed", "LuminanceAdjustmentOrange", "LuminanceAdjustmentYellow",
    "LuminanceAdjustmentGreen", "LuminanceAdjustmentAqua", "LuminanceAdjustmentBlue",
    "LuminanceAdjustmentPurple", "LuminanceAdjustmentMagenta",
    "SplitToningShadowHue", "SplitToningShadowSaturation",
    "SplitToningHighlightHue", "SplitToningHighlightSaturation", "SplitToningBalance",
    "ColorGradeGlobalHue", "ColorGradeGlobalSat", "ColorGradeGlobalLum",
    "ColorGradeShadowLum", "ColorGradeMidtoneHue", "ColorGradeMidtoneSat",
    "ColorGradeMidtoneLum", "ColorGradeHighlightLum", "ColorGradeBlending",
    "Sharpness", "SharpenRadius", "SharpenDetail", "SharpenEdgeMasking",
    "LuminanceSmoothing", "ColorNoiseReduction", "ColorNoiseReductionDetail",
    "ColorNoiseReductionSmoothness",
    "PostCropVignetteAmount", "PostCropVignetteMidpoint", "PostCropVignetteFeather",
    "PostCropVignetteRoundness", "PostCropVignetteStyle", "PostCropVignetteHighlightContrast",
    "GrainAmount", "GrainSize", "GrainFrequency",
    "DefringePurpleAmount", "DefringePurpleHueLo", "DefringePurpleHueHi",
    "DefringeGreenAmount", "DefringeGreenHueLo", "DefringeGreenHueHi",
    "ShadowTint", "RedHue", "RedSaturation", "GreenHue", "GreenSaturation",
    "BlueHue", "BlueSaturation",
    "ConvertToGrayscale",
))

_CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def read_embedded(raw_path: str) -> bytes | None:
    """The XMP packet inside a DNG/TIFF container, or None.

    Lightroom writes develop settings *into* a DNG — sidecars exist only
    beside proprietary raws — so a DNG's edits are read from its own bytes.
    A bounded scan: chunks in, at most 16 MiB of packet out.
    """

    start_tag, end_tag = b"<x:xmpmeta", b"</x:xmpmeta>"
    chunk_size = 1 << 22
    overlap = len(start_tag)
    buf = b""
    packet_start = -1
    collected = bytearray()
    with open(raw_path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return None
            buf = buf[-overlap:] + chunk if packet_start < 0 else chunk
            if packet_start < 0:
                found_at = buf.find(start_tag)
                if found_at < 0:
                    continue
                packet_start = found_at
                collected.extend(buf[found_at:])
            else:
                collected.extend(chunk)
            end = collected.find(end_tag)
            if end >= 0:
                return bytes(collected[: end + len(end_tag)])
            if len(collected) > (1 << 24):
                return None


def read_sidecar(path: str) -> dict[str, object] | None:
    """Every crs fact in one sidecar, keys spelled as Lightroom spells them.

    Scalars arrive as strings exactly as written (``"+0.50"`` stays
    ``"+0.50"`` — round-tripping is transcription). Array-valued settings
    (tone curves, point colors, looks) arrive as lists of their item
    strings. Returns None for a file that is not a crs sidecar.
    """

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    return _facts(root)


def read_within(path: str) -> dict[str, object] | None:
    """The crs facts a self-carrying photograph holds inside itself.

    Lightroom writes sidecars only beside proprietary raws; a DNG's settings
    live in its own XMP packet. Same facts, same spellings, different door.
    """

    packet = read_embedded(path)
    if packet is None:
        return None
    try:
        root = ET.fromstring(packet)
    except ET.ParseError:
        return None
    return _facts(root)


def _facts(root) -> dict[str, object] | None:
    found: dict[str, object] = {}
    for description in root.iter(f"{{{_RDF}}}Description"):
        for name, value in description.attrib.items():
            if name.startswith(f"{{{_CRS}}}"):
                found[name[len(_CRS) + 2:]] = value
        for child in description:
            if not child.tag.startswith(f"{{{_CRS}}}"):
                continue
            key = child.tag[len(_CRS) + 2:]
            items = [li.text or "" for li in child.iter(f"{{{_RDF}}}li")]
            if items:
                found[key] = items
            elif child.text and child.text.strip():
                found[key] = child.text.strip()
    return found or None


def settings(conn, digest: str) -> dict:
    """The photograph's current edit — the newest authoritative decision."""

    said = decisions.latest(conn, str(digest), FAMILY)
    return said if isinstance(said, dict) else {}


def _typed(value):
    """A sidecar's string, as the number or truth it spells; lists and
    dicts pass through for the renderer's own parsers."""

    if isinstance(value, str):
        held = value.strip()
        if held.lower() in ("true", "false"):
            return held.lower() == "true"
        try:
            number = float(held)
        except ValueError:
            return held
        return int(number) if number.is_integer() and "." not in held else number
    return value


def fragment(held: dict) -> str | None:
    """The renderable part of an edit, as the canonical recipe fragment:
    the RENDERED crs subset, typed, defaults dropped, keys sorted.

    None when the edit changes no pixels a rendition shows — a full-frame
    crop is not an edit, a zeroed slider is not an edit, and a photograph
    with neither keeps the recipe (and the tiles) it always had.
    """

    kept: dict[str, object] = {}
    for key in sorted(RENDERED & set(held)):
        value = _typed(held[key])
        if value in (0, 0.0, False, "", None):
            continue          # a slider at rest says nothing
        kept[key] = value
    crop = None
    if any(key in kept for key in CROP_KEYS):
        try:
            crop = [round(float(_typed(held.get(key, default))), 6)
                    for key, default in zip(CROP_KEYS, (0.0, 0.0, 1.0, 1.0))]
        except (TypeError, ValueError):
            crop = None
        for key in CROP_KEYS:
            kept.pop(key, None)
        if crop is not None:
            left, top, right, bottom = crop
            if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
                crop = None
            elif crop == [0.0, 0.0, 1.0, 1.0]:
                crop = None
        if crop is not None:
            kept.update(zip(CROP_KEYS, crop))
    if not kept:
        return None
    return json.dumps(kept, separators=(",", ":"), sort_keys=True)


def project(conn, digest: str) -> str | None:
    """Write the photograph's current fragment to its rows' develop column.

    Returns the fragment. Every row of the identity takes it — a photo filed
    in two folders is one photograph, edited once.
    """

    held = fragment(settings(conn, digest))
    conn.execute(
        "UPDATE images SET develop = ? WHERE content_hash = ?", (held, str(digest)))
    return held


def reindex(conn) -> int:
    """Rebuild every develop column from the log — the boot-time answer to
    'the log is the truth and the column is an index over it'."""

    projected = 0
    conn.execute("UPDATE images SET develop = NULL WHERE develop IS NOT NULL")
    for row in conn.execute(
        "SELECT DISTINCT subject FROM decisions WHERE family = ?", (FAMILY,)):
        if project(conn, row["subject"]) is not None:
            projected += 1
    conn.commit()
    return projected


def edit(conn, digest: str, patch: dict) -> dict:
    """The owner changes an edit: the current settings plus this patch, as
    one appended decision. A None value removes its key."""

    held = dict(settings(conn, digest))
    for key, value in patch.items():
        if value is None:
            held.pop(key, None)
        else:
            held[key] = value
    decisions.decide(conn, str(digest), FAMILY, held)
    project(conn, digest)
    conn.commit()
    return held


# Formats that carry their settings inside the photograph itself. Lightroom
# writes sidecars beside proprietary raws and XMP packets into these.
SELF_CARRYING = (".dng",)


def adopt(conn, root: str, sidecar_tails, rewritten_tails=()) -> int:
    """Read what the sweep saw that may carry settings, into decisions.

    Three kinds of carrier: every sidecar the walk noticed (paired to its
    photograph by stem); every self-carrying photograph the log has never
    heard from; and every one the sweep saw rewritten — Lightroom saving
    metadata writes *into* a DNG, so the rewrite is the sidecar changing.

    A carrier whose settings differ from the last file-authored decision
    appends one — unchanged files append nothing, so re-sweeping is free.
    A self-carrying photograph with nothing inside gets one empty decision:
    the receipt that we looked, which is what keeps a sweep from re-reading
    megabytes of every DNG forever.
    """

    plans: dict[str, bool] = {}      # tail -> carries its own settings
    for tail in sidecar_tails or ():
        plans[str(tail)] = False
    for tail in rewritten_tails or ():
        if str(tail).lower().endswith(SELF_CARRYING):
            plans[str(tail)] = True
    marks = ",".join("?" * len(SELF_CARRYING))
    for row in conn.execute(
        "SELECT i.tail FROM images i"
        " WHERE i.tail IS NOT NULL AND i.content_hash IS NOT NULL AND i.vc_of IS NULL"
        f" AND i.file_ext IN ({marks})"
        " AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.subject = i.content_hash"
        "                 AND d.family = ? AND d.by = ?)",
        (*SELF_CARRYING, FAMILY, BY_FILE)):
        plans[row["tail"]] = True
    if not plans:
        return 0

    stems: dict[str, str] = {}
    for row in conn.execute(
        "SELECT tail, content_hash FROM images"
        " WHERE tail IS NOT NULL AND content_hash IS NOT NULL"):
        stems[os.path.splitext(row["tail"])[0]] = row["content_hash"]
    adopted = 0
    for tail, carries in sorted(plans.items()):
        digest = stems.get(os.path.splitext(tail)[0])
        if digest is None:
            continue
        path = os.path.join(root, tail.replace("/", os.sep))
        if carries:
            # The unheard live on whatever drive holds them; a file that is
            # not under this root waits for its own drive's sweep.
            if not os.path.isfile(path):
                continue
            held = read_within(path) or {}
        else:
            held = read_sidecar(path)
            if held is None:
                continue
        before = _last_adopted(conn, digest)
        if before == held:
            continue
        decisions.decide(conn, digest, FAMILY, held, by=BY_FILE)
        project(conn, digest)
        adopted += 1
    if adopted:
        conn.commit()
    return adopted


def _last_adopted(conn, digest: str) -> dict | None:
    row = conn.execute(
        "SELECT value FROM decisions WHERE subject = ? AND family = ? AND by = ?"
        " ORDER BY at DESC, id DESC LIMIT 1",
        (str(digest), FAMILY, BY_FILE),
    ).fetchone()
    said = decisions.loaded(row)
    return said if isinstance(said, dict) else None


def parts(fragment_json: str | None) -> dict:
    """A stored fragment back as its typed dict, or empty."""

    if not fragment_json:
        return {}
    try:
        held = json.loads(fragment_json)
    except (TypeError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def write_sidecar(conn, digest: str, photo_path: str) -> dict:
    """The round trip's other half: the photograph's whole story into the
    sidecar beside it — settings, stars, pick, words — the LRTimelapse
    discipline.

    The crs surface becomes exactly ours (Lightroom's spellings for what it
    wrote, Adobe's spelling for what the owner changed here); every other
    block in the file — camera facts, keywords, history — is kept element
    for element. A photograph without a sidecar gets a fresh, crs-only one
    Lightroom reads the same way.
    """

    from features.develop import xmp_write

    ours = settings(conn, digest)
    target = os.path.splitext(str(photo_path))[0] + ".xmp"
    for prefix, uri in (
        ("x", "adobe:ns:meta/"), ("rdf", _RDF), ("crs", _CRS),
        ("xmp", _XMP),
        ("tiff", "http://ns.adobe.com/tiff/1.0/"),
        ("exif", "http://ns.adobe.com/exif/1.0/"),
        ("dc", _DC),
        ("lr", _LR),
        ("aux", "http://ns.adobe.com/exif/1.0/aux/"),
        ("photoshop", "http://ns.adobe.com/photoshop/1.0/"),
        ("xmpMM", "http://ns.adobe.com/xap/1.0/mm/"),
        ("stEvt", "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"),
        ("crd", "http://ns.adobe.com/camera-raw-defaults/1.0/"),
    ):
        ET.register_namespace(prefix, uri)
    root = None
    if os.path.isfile(target):
        try:
            root = ET.parse(target).getroot()
        except (ET.ParseError, OSError):
            root = None
    if root is None:
        root = ET.fromstring(xmp_write.serialize({}))
    description = next(root.iter(f"{{{_RDF}}}Description"), None)
    if description is None:
        return {"status": "nothing", "target": target}
    for name in [n for n in description.attrib if n.startswith(f"{{{_CRS}}}")]:
        del description.attrib[name]
    for child in [c for c in description if c.tag.startswith(f"{{{_CRS}}}")]:
        description.remove(child)
    if ours:
        xmp_write._append_resource(description, ours)
    _tell_lightroom(conn, digest, description)
    payload = ET.tostring(root, encoding="utf-8")
    from pathlib import Path

    changed = xmp_write._atomic_write(Path(target), payload)
    return {"status": "written" if changed else "unchanged", "target": target}


_XMP = "http://ns.adobe.com/xap/1.0/"
_DC = "http://purl.org/dc/elements/1.1/"
_LR = "http://ns.adobe.com/lightroom/1.0/"


def _tell_lightroom(conn, digest: str, description) -> None:
    """The library's own verdicts, in the spellings Lightroom reads.

    Stars become ``xmp:Rating`` and a pick becomes the Green color label —
    XMP has no flag field Lightroom will read; flags never leave a catalog,
    and the color label is the honest visible stand-in. The taught words and
    people become ``dc:subject`` keywords (and the flat half of
    ``lr:hierarchicalSubject``), *unioned* with whatever keywords the file
    already carried — Lightroom's own vocabulary is never dropped.
    """

    import json

    row = conn.execute(
        "SELECT stars, status FROM images WHERE content_hash = ? LIMIT 1",
        (str(digest),)).fetchone()
    if row is None:
        return
    rating = f"{{{_XMP}}}Rating"
    if int(row["stars"] or 0):
        description.set(rating, str(int(row["stars"])))
    else:
        description.attrib.pop(rating, None)
    label = f"{{{_XMP}}}Label"
    if row["status"] == "picked":
        description.set(label, "Green")
    elif description.get(label) == "Green":
        del description.attrib[label]

    words: set[str] = set()
    for held in conn.execute(
        "SELECT value FROM cache WHERE kind IN ('alike', 'people')"
        " AND state = 'ready' AND hash = ?", (str(digest),)):
        try:
            said = json.loads(held["value"])
        except (TypeError, ValueError):
            continue
        words |= {str(w) for w in (said if isinstance(said, list) else []) if str(w)}
    if not words:
        return
    for tag in (f"{{{_DC}}}subject", f"{{{_LR}}}hierarchicalSubject"):
        element = description.find(tag)
        if element is None:
            element = ET.SubElement(description, tag)
        bag = element.find(f"{{{_RDF}}}Bag")
        if bag is None:
            bag = ET.SubElement(element, f"{{{_RDF}}}Bag")
        carried = {li.text for li in bag.iter(f"{{{_RDF}}}li") if li.text}
        for word in sorted(words - carried):
            ET.SubElement(bag, f"{{{_RDF}}}li").text = word


def crop_of(fragment_json: str | None) -> tuple[float, float, float, float] | None:
    """A stored fragment's crop as (left, top, right, bottom), or None."""

    held = parts(fragment_json)
    if not all(key in held for key in CROP_KEYS):
        return None
    try:
        return tuple(float(held[key]) for key in CROP_KEYS)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None

