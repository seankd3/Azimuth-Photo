# Journeys

One file per thing a photographer does, each a probe `native_proof.py` runs
against the proof home and ends with a screenshot. The gallery is read side by
side with the last round's by the taste reviewer; nothing diffs pixels.

    web\.venv\Scripts\python.exe scripts\native_proof.py <home> gallery\cull.png --probe scripts\journeys\cull.js --wait 20

With no desktop (a cloud session), the same probe runs against the harness
page in headless Chromium and proves shape and behaviour, never pixels:

    node scripts/harness_proof.mjs gallery/cull.png --probe scripts/journeys/cull.js

A journey is added when a real task is not covered, never in advance.
