"""What each stage of the pipeline records.

A stage is a name and a function that returns JSON. Nothing else. Add one by
writing the function and listing it in :data:`STAGES`.

App modules are imported inside the functions, not at module scope, because
``harness.env.apply()`` must set the runtime paths before anything reads them.
"""

from __future__ import annotations

from pathlib import Path

from harness import corpus, facts


def _relative(path: Path) -> str:
    """A stable key for a file, wherever the corpus happens to live."""

    return path.name


# --------------------------------------------------------------------------
# Identity — one content hash, used everywhere. Already the cleanest module in
# the pipeline; this freezes it so it stays that way.
# --------------------------------------------------------------------------

def identity() -> dict:
    from photo.identity import compute_hash_pair

    recorded = {}
    for path in corpus.build():
        content, full = compute_hash_pair(path)
        recorded[_relative(path)] = {
            "content_hash": content,
            "full_hash": full,
            "size": path.stat().st_size,
        }
    return recorded


# --------------------------------------------------------------------------
# Format — "is this a RAW?" Today fourteen implementations answer this, over
# six different extension sets. Record what every one of them says, so the
# consolidation can prove exactly which files change verdict and which do not.
# --------------------------------------------------------------------------

def _extension_sets() -> dict[str, set[str]]:
    from features.develop import hdr, importer, rawproc, xmp_write
    from features.imports import taxonomy
    from features.stacks import builders
    from thumbnails import config as thumb_config

    return {
        "thumbnails.config": set(thumb_config.RAW_EXTENSIONS),
        "develop.rawproc": set(rawproc.RAW_EXTENSIONS),
        "develop.rawproc.unfitted": set(rawproc.UNFITTED_RAW_EXTENSIONS),
        "develop.hdr": set(hdr.RAW_EXTENSIONS),
        "develop.importer": set(importer.RAW_EXTENSIONS),
        "develop.xmp_write": set(xmp_write._RAW_EXTENSIONS),
        "imports.taxonomy": set(taxonomy.RAW_CAMERA_EXTENSIONS),
        "imports.taxonomy.unambiguous": set(taxonomy.UNAMBIGUOUS_RAW_EXTENSIONS),
        "stacks.builders": {f".{ext}" for ext in builders.RAW_EXTS},
    }


def kind() -> dict:
    from features.develop.rawproc import is_raw_path
    from thumbnails.config import RAW_EXTENSIONS
    from thumbnails.generation import is_raw_original

    sets = _extension_sets()
    recorded = {}
    for path in corpus.build():
        ext = path.suffix.lower()
        by_extension = {name: ext in members for name, members in sorted(sets.items())}
        recorded[_relative(path)] = {
            "ext": ext,
            # The one rule that reads the bytes. Everything else guesses.
            "by_content": is_raw_original(str(path), ext, RAW_EXTENSIONS),
            "develop.is_raw_path": is_raw_path(path),
            "by_extension": by_extension,
            "verdicts_disagree": len({*by_extension.values()}) > 1,
        }
    return recorded


# --------------------------------------------------------------------------
# Decode — the same RAW through every decoder the app owns. They do not agree
# today; the goldens say precisely how, and prove what the one decoder changes.
# --------------------------------------------------------------------------

def _decoders():
    from features.develop.render import decode_full_resolution
    from features.develop.rawproc import decode_base
    from thumbnails.generation import extract_embedded_raw_preview
    from thumbnails.raw_ops import demosaic_raw_for_thumbnail

    def embedded(path: Path):
        preview = extract_embedded_raw_preview(str(path))
        return facts.image(preview) if preview is not None else {"absent": True}

    def thumbnail(path: Path):
        return facts.image(demosaic_raw_for_thumbnail(str(path), 1024))

    def develop_base(path: Path):
        array, meta = decode_base(path)
        recorded = facts.pixels(array)
        recorded["meta_keys"] = sorted(str(key) for key in meta)
        return recorded

    def export(path: Path):
        return facts.pixels(decode_full_resolution(path))

    return {
        "embedded": embedded,
        "thumbnail": thumbnail,
        "develop_base": develop_base,
        "export": export,
    }


def decode() -> dict:
    recorded = {}
    for path in corpus.build():
        per_decoder = {}
        for name, run in _decoders().items():
            try:
                per_decoder[name] = run(path)
            except Exception as exc:  # a refusal is a recorded fact
                per_decoder[name] = facts.failure(exc)
        recorded[_relative(path)] = per_decoder
    return recorded


# --------------------------------------------------------------------------
# Rendition — the cache key. Three schemes exist and two of them are built from
# different inputs for the same file, so they can disagree about whether a
# cached preview is still good.
# --------------------------------------------------------------------------

def rendition() -> dict:
    """The key, not its value.

    A signature contains the file's mtime, so the digest itself is different on
    every corpus rebuild and would make a useless golden. What is stable — and
    what actually breaks caching — is whether the two schemes agree with each
    other, and whether the key's idea of RAW matches the decoder's.
    """

    from thumbnails import source_identity
    from thumbnails.config import (
        CACHE_VERSION,
        FULL_TIER,
        RAW_EXTENSIONS,
        SIZES,
        THUMB_QUALITY,
        THUMB_TIERS,
    )
    from thumbnails.generation import is_raw_original

    def signature(bits: str, tier: str, image_id: int) -> str:
        return source_identity.build_source_signature_from_bits(
            bits,
            tier,
            image_id,
            cache_version=CACHE_VERSION,
            full_tier=FULL_TIER,
            sizes=SIZES,
            thumb_quality=THUMB_QUALITY,
        )

    recorded = {}
    for index, path in enumerate(corpus.build(), start=1):
        stat = path.stat()
        from_stat = source_identity.get_source_bits(str(path))
        from_catalog, _size, _missing = source_identity.source_bits_from_catalog_metadata(
            str(path), stat.st_size, stat.st_mtime
        )
        agree = {
            tier: signature(from_stat, tier, index) == signature(from_catalog, tier, index)
            for tier in (*THUMB_TIERS, FULL_TIER)
        }
        ext = path.suffix.lower()
        # thumbnails/__init__.py:336 stamps the RAW render version onto lg and
        # full keys by extension. generation.py:113 picks the decoder by content.
        # When these disagree the cache key promises pixels the decoder will not
        # produce — which is the state of all 1,306 mislabelled files today.
        key_says_raw = ext in RAW_EXTENSIONS
        pixels_say_raw = is_raw_original(str(path), ext, RAW_EXTENSIONS)
        recorded[_relative(path)] = {
            "cache_version": CACHE_VERSION,
            "stat_scheme": from_stat.split("|")[0] if from_stat.startswith("missing") else "size|mtime_ns|path",
            "catalog_scheme": from_catalog.split("|")[0],
            "schemes_agree_per_tier": agree,
            "key_says_raw": key_says_raw,
            "pixels_say_raw": pixels_say_raw,
            "key_and_pixels_disagree": key_says_raw != pixels_say_raw,
        }
    return recorded


# --------------------------------------------------------------------------
# Preview — what the library actually shows. load_source_image is the one
# function the grid, the loupe and pregeneration all reach, and it chooses
# between the embedded JPEG and a demosaic per tier. The tier at which that
# choice flips is the thing a decoder consolidation must not move by accident.
# --------------------------------------------------------------------------

def preview() -> dict:
    from thumbnails.config import JPEG_EXTENSIONS, RAW_EXTENSIONS, SIZES
    from thumbnails.generation import load_raw_preview, load_source_image

    recorded = {}
    for path in corpus.build():
        per_tier = {}
        for tier, target in sorted(SIZES.items(), key=lambda item: item[1]):
            entry: dict = {}
            try:
                image = load_source_image(
                    str(path),
                    target,
                    jpeg_extensions=set(JPEG_EXTENSIONS),
                    raw_extensions=set(RAW_EXTENSIONS),
                )
                entry = facts.image(image)
                # Which branch produced it: the camera's own JPEG, or ours.
                entry["from_embedded_preview"] = load_raw_preview(str(path), target) is not None
            except Exception as exc:
                entry = facts.failure(exc)
            per_tier[tier] = entry
        recorded[_relative(path)] = per_tier
    return recorded


# --------------------------------------------------------------------------
# Routes — the app's public surface. Two files register routers today and the
# order between them is load-bearing but untyped, so this is what says a
# deletion or a re-wiring dropped an endpoint. Importing the app at all is also
# the cheapest proof that the module graph still resolves.
# --------------------------------------------------------------------------

METHODS = ("get", "post", "put", "patch", "delete")


def routes() -> dict:
    import app as app_module

    spec = app_module.app.openapi()
    recorded = {}
    for path, operations in spec["paths"].items():
        for method, operation in sorted(operations.items()):
            if method not in METHODS:
                continue
            parameters = operation.get("parameters") or []
            recorded[f"{method.upper()} {path}"] = {
                "params": sorted(str(item.get("name")) for item in parameters),
                "required_params": sorted(
                    str(item.get("name")) for item in parameters if item.get("required")
                ),
                "has_body": "requestBody" in operation,
            }
    return recorded


STAGES = {
    "identity": identity,
    "kind": kind,
    "decode": decode,
    "rendition": rendition,
    "preview": preview,
    "routes": routes,
}
