"""What a photo is.

The domain layer. It knows about files, formats and identity, and nothing
about routes, workers or the database. Nothing here may import ``features``,
``thumbnails`` or ``data`` — if something in this package needs one of those,
it belongs a layer up.
"""
