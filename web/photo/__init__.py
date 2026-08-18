"""Pure reads of photo bytes.

Format detection and bounded EXIF parsing live here. Durable identity,
locations, copies, and decisions belong to ``model``; scheduling and cached
projection belong above this package. Nothing here imports either.
"""
