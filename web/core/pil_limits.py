"""Pillow process-wide configuration.

Import this alongside every `from PIL import ...` that opens catalog images.
The library is trusted local data and panoramas routinely exceed Pillow's
decompression-bomb default, so the limit is disabled globally. This used to
ride on thumbnails/__init__ importing PIL at boot; once PIL moved off the
boot path (66a0352f8) each PIL entry point must opt in explicitly.
"""

from PIL import Image

Image.MAX_IMAGE_PIXELS = None
