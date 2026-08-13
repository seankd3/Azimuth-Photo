"""The derivation key: (content_hash, kind, recipe).

A derivation is a pure function of what a photo currently is. ``content_hash``
names the photo, ``kind`` names the artifact (for byte artifacts it is the
tier: sm/md/lg/full), and ``recipe`` is one hash of everything that changes
the bytes. Same key, same bytes — on any machine, at any time.

The recipe is what makes an edit reach its own thumbnail: a photo's develop
fingerprint is a recipe input, so saving an edit changes the key and the old
tile stops being the answer. The previous identity
(``thumbnails/source_identity.py``) hashed the file's path and mtime instead —
so a rename invalidated a perfectly good tile, and an edit invalidated
nothing.
"""

from __future__ import annotations

import hashlib


def recipe_for(
    *,
    cache_version: str,
    kind: str,
    pixels: int,
    quality: int,
    develop_fingerprint: str = "",
) -> str:
    """One hash of every input that changes a derived artifact's bytes.

    ``cache_version`` is the code version (bump it and every artifact is owed
    again); ``pixels`` and ``quality`` are the tier's output parameters (0
    where a kind has no such knob); ``develop_fingerprint`` is "" for a photo
    with no develop settings and the settings row's updated_at once it has
    them. Nothing else may enter: not the image id (identity lives beside the
    recipe, not inside it) and not the file's path or mtime (the content hash
    is the file identity).

    test_rendition_recipe pins this input list; extending it is a deliberate
    act with a test change, never a drive-by.
    """

    material = f"{cache_version}|{kind}|{int(pixels)}|{int(quality)}|{develop_fingerprint}"
    return hashlib.sha1(material.encode("utf-8", "surrogateescape")).hexdigest()
