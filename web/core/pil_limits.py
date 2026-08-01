"""Pillow process-wide configuration.

Import this alongside every `from PIL import ...` that opens catalog images.
The library is trusted local data and panoramas routinely exceed Pillow's
decompression-bomb default, so the limit is disabled globally. This used to
ride on thumbnails/__init__ importing PIL at boot; once PIL moved off the
boot path (66a0352f8) each PIL entry point must opt in explicitly.

A photo that is missing its final marker is still a photo. Measured on the
owner's archive: 2,480 of 44,521 JPEGs end without the two-byte end-of-image
marker — almost all of them Google Takeout exports — and Pillow refused every
one with "image file is truncated (0 bytes not processed)". *Zero bytes not
processed*: the picture is entirely there, only the terminator is absent. All
twelve sampled decoded at full resolution once partial loading was allowed.
Refusing them meant those photos could never get a thumbnail and the bulk
generator burned its budget rediscovering that on every pass.

Showing what is present beats showing nothing. A file damaged badly enough to
lack real image data still raises, so this widens what can be displayed without
hiding genuine corruption.
"""

from PIL import Image, ImageFile

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True
