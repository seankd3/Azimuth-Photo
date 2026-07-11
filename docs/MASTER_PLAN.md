# photoArchive MASTER PLAN — the best professional photo app ever shipped
2026-07-11. The bar, by competitor: beat **Lightroom Classic** on editing depth and speed, beat
**Aftershoot** on culling intelligence, beat **Lightroom CC** on anywhere-access simplicity, beat
**Affinity Photo** on the retouch cases photographers actually use, beat **darktable** on power with
none of the pain, and beat all of them on publishing, ownership, and taste.

Legend: ✅ shipped · 🔨 in flight (lane running/scheduled) · 🎯 planned (spec exists) · 💡 designed here.

## Pillar 1 — Editing depth (Lightroom Classic and beyond)
- ✅ Full RAW pipeline (incl. JPEG-XL lossy DNGs nothing else decodes, orientation-correct), WebGL live
  editing, numpy exports, twin parity tests
- ✅ Basic/Tone/Presence/ToneCurve/HSL/B&W/Detail/Effects/Crop, histogram+clipping, before/after, copy/paste
- ✅ Masks: brush/linear/radial/luminance/color-range + AI subject/sky, local Light/Color/Effects, rubylith
- ✅ Presets (+ user's real LR preset library, hover live preview), camera-fitted color profiles (fitted
  tone TRUSTED via Looks refit), lens distortion/vignetting, HDR merge, pano merge
- ✅ Color grading wheels · noise reduction (incl. Detail/Contrast) · defringe · distortion auto-crop
- ✅ Heal/clone spots · Looks rendering · export dialog (resize/sharpen/pattern/save-to-library) + batch sync
- ✅ Transform/Upright (Hough auto-level, guided, homography twins) · Lens panel + Calibration panel
- ✅ Sharpen edge-masking · side-by-side Before/After + Reference view · soft proofing
- ✅ Virtual copies + Snapshots + History rail (Ctrl-')
- ✅ **Film emulation engine** (spec §26) — physically modeled per-stock: H&D curves, DIR couplers,
  per-layer grain, halation (CineStill 800T red glow from the physics), print transform; 8 stocks tuned
  against Sean's real San Marcos lab scans; Film panel live in both renderers
- ✅ **Edit anything**: Develop works on JPEG/TIFF/PNG/WebP (display-referred base) — whole library editable
- 🔨 Editing depth wave (spec §29): zoom/proof tiles, auto tone, range masks, preset live-preview perf,
  latency budgets
- 💡 Process-version safety: settings carry a pipeline version; old edits always re-render identically
- 💡 GPU export queue (batch exports render on the 3050 Ti via wgpu later; CPU fine now)

## Pillar 2 — Culling intelligence (Aftershoot-killer)
- ✅ Elo ranking + Bayesian taste model (unique — nobody else learns YOUR taste from YOUR duels)
- ✅ Refine/duel modes, stacks (burst/variant/crosssource), auto-advance flag flow, filmstrip at 9ms/step
- ✅ Version stacks: RAW↔edit lineage (V cycles; save-to-library exports join the stack)
- ✅ Technical quality scorer (face-region sharpness/clip/blur, proven sane on real frames)
- ✅ Review-first auto-cull + cull brief (A accept / S skip) — LIVE on prod with 5k+ suggestions;
  all-stacks payload cached (uncached recompute stalled boot ~15s)
- 💡 Survey view (N-up compare with eliminate-on-click)

## Pillar 3 — The unified photo model (Renditions, spec §27)
- ✅ **Bidirectional XMP write-back**: our edits written as Adobe-schema XMP (sidecar + guarded embedded,
  round-trip proven), keywords included — open our edits IN Lightroom anytime. Zero lock-in.
- ✅ Version stacks unify RAW/exports/virtual copies as lineage (renditions groundwork)
- 💡 Photo = capture moment; grid shows photos, not files; old LR exports become editable bookmarks
- 💡 Full-library RAW import (82k raws) as a background campaign with progress + nightly XMP re-sync

## Pillar 4 — Organization & search (beyond LR CC)
- ✅ Semantic search (3-engine RRF + captions), faces/people, smart collections, tags-as-facets
- ✅ Keywords/IPTC: hierarchical keyword panel + spray-painter, full IPTC edit, XMP keyword write-back
- ✅ Geodata: EXIF GPS + camera-metadata backfill, trail interpolation, Google Timeline importer;
  map view live with real markers
- ✅ Timeline view (date river with density graph) · saved views (query+sort+layout workspaces)

## Pillar 5 — Ingest & trust
- ✅ Sources + rescan, safe trash, DB migrations with backups
- ✅ Catalog time machine: gzip-verified DB snapshots + retention + integrity checksums, one-click restore
- 🔨 Watched folders (auto-import on change) — lane running
- 💡 Card import wizard with checksum + dual-destination backup
- 💡 Phone → archive: PWA share-target + auto-upload queue over Tailscale (Pixel loop closes)
- 💡 Integrity audit: checksums per original, bit-rot scan on the Expansion drive schedule

## Pillar 6 — Publishing & delivery (already ahead; extend the lead)
- ✅ Website publishing (drag-to-publish, frozen snapshots, review-updates), private links with passwords,
  favorites proofing, share analytics
- ✅ Client galleries v2 (gallery editor: per-link watermarks, download sizes, protected selects)
- ✅ Export presets (web/print/insta crops) · save-to-library exports join version stacks
- 💡 Publish pipelines (edit → auto-export → website slot)
- 💡 Print-ready exports: soft-proof + border/paper templates (the useful 10% of LR's Print module)

## Pillar 7 — Performance & polish (always-on lanes)
- ✅ 0.4s RAW preview decode, 9ms loupe nav, instant slider response, 600+-test suite
- ✅ Measured perf wins: sparse gamut clip, gzip-1 bases, month index, embed-cache signature, autocull
  payload cache; bench harness at `web/perf/bench.py`
- 🔨 Keyboard shortcut sheet overlay (?) — lane running
- 💡 Standing perf budget CI: decode/nav/slider timings asserted in tests, regressions fail the suite
- 💡 Progressive base loading (jpg → half-bin → full-bin), speculative neighbor pregen everywhere
- 💡 Weekly polish lane: screenshot-diff sweep of every surface vs Notion-level checklist, fix on sight
- 💡 Keyboard-complete: every action reachable without mouse

## Pillar 8 — Field workflow (satellite + hub sync)
Spec: [`FIELD_SPEC.md`](FIELD_SPEC.md) (v1, frozen).
- 🔨 Satellite mode: lightweight photoArchive in the field, hub sync contract over Tailscale
  (content hashing, read-through media, sync worker) — lanes building `web/features/sync/` now
- 🎯 Conflict-safe merge of field edits/culls back into the hub catalog

## Sequencing
Wave now (running): field sync (FIELD_SPEC lanes), watched folders, shortcut sheet, editing depth
wave (spec §29). Shipped waves dev1–7 took the app from pipeline → masks → presets/profiles →
grading/heal/export → transform/edit-anything/autocull/time-machine → film/lens/calibration/
compare/proof/XMP → keywords/galleries/geodata/perf. Then: renditions grid model, full-library
import campaign, publish pipelines, perf budget CI. Merge to main once the current wave lands
green; after that main rides each verified wave.
