"""Golden-output harness for the end-to-end image pipeline.

The refactor deletes layers. This says whether a deletion changed what the app
produces. Every stage records a small, stable fact about one image's journey —
its identity, its format, its pixels, its cache key, its bytes on the wire — and
``--check`` fails when any of them moves.

    python -m harness --record     # write goldens (review the diff)
    python -m harness --check      # fail if anything moved

A step that means to change a golden updates it in the same commit and says why
in the message. A step that does not mean to change one has a bug.
"""
