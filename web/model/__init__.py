"""The core: what Azimuth knows about a photo.

Four kinds of fact — what it is, where copies are, what the owner decided, and
what we computed — over five tables and seven functions. `docs/CORE.md` is the
whole design; this package is it, made real.

Tables arrive with the step that needs them, so `schema.sql` never describes
something that is not yet true.
"""
