# Aspect-at-scan — spec (v1, frozen 2026-07-16)

## Why

Placeholder cards (docs/GRID_PLACEHOLDERS_SPEC.md) fall back to 3:2 when a photo's
aspect ratio is unknown, which is every photo for the first seconds after import.
When real aspects arrive the justified layout re-packs once — visible as a single
grid reflow early in every import. One reflow is acceptable; zero is better.

## Behavior

1. **Read dimensions during scan registration.** The scanner already stats every
   file; for formats where dimensions are cheap (EXIF/TIFF header parse — CR3/DNG/
   JPEG/HEIC via the same reader the orientation scan uses), capture width/height
   (orientation-corrected) in the scan batch and store aspect_ratio on the images
   row at insert. Budget: must not slow scan registration measurably — if a file's
   header read exceeds ~5ms, skip it (metadata worker backfills as today).
2. **Placeholder cards therefore have final aspect from first render** for the
   overwhelming majority of photos; the 3:2 fallback remains for the stragglers.
3. **No new workers, no new columns beyond aspect_ratio if it doesn't already
   exist** (check: the rankings payload gained aspect_ratio in the placeholder
   work — reuse whatever storage backs it).

## Acceptance

- Fresh import of the 89-RAW test folder: zero grid re-packs after first paint
  (card positions stable from the first placeholder render through full sharpen;
  measure boundingBoxes at first paint and after all previews land).
- Scan registration throughput within 10% of pre-change (bench scan phase or time
  the register step on the test folder).
- Existing placeholder contract tests unmodified and green.
