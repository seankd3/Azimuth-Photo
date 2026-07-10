# photoArchive MASTER PLAN — the best professional photo app ever shipped
2026-07-10. The bar, by competitor: beat **Lightroom Classic** on editing depth and speed, beat
**Aftershoot** on culling intelligence, beat **Lightroom CC** on anywhere-access simplicity, beat
**Affinity Photo** on the retouch cases photographers actually use, beat **darktable** on power with
none of the pain, and beat all of them on publishing, ownership, and taste.

Legend: ✅ shipped · 🔨 in flight (lane running/scheduled) · 🎯 planned (spec exists) · 💡 designed here.

## Pillar 1 — Editing depth (Lightroom Classic and beyond)
- ✅ Full RAW pipeline (incl. JPEG-XL lossy DNGs nothing else decodes), WebGL live editing, numpy exports, twin parity tests
- ✅ Basic/Tone/Presence/ToneCurve/HSL/B&W/Detail(sharpen)/Effects/Crop, histogram+clipping, before/after, copy/paste
- ✅ Masks: brush/linear/radial/luminance/color-range + AI subject/sky, local Light/Color/Effects, rubylith
- ✅ Presets (+ user's real LR preset library), camera-fitted color profiles, lens distortion/vignetting, HDR merge
- 🔨 Color grading wheels · noise reduction · defringe · distortion auto-crop (lane)
- 🔨 Heal/clone spots (lane) · Looks rendering + trusted tone refit (lane) · pano merge (lane)
- 🔨 Export dialog (resize/sharpen/pattern/save-to-library) + batch sync (lane)
- 🎯 Transform/Upright (auto-level, guided, perspective) · Lens UI panel + Manual tab · Calibration panel
- 🎯 Detail completion (sharpen Masking, NR Detail/Contrast) · side-by-side Before/After + Reference view
- 🎯 Virtual copies + Snapshots + History panel (left rail) · soft proofing with gamut warning
- 🎯 **Film emulation engine** (spec §26) — physically modeled per-stock: H&D curves, DIR couplers,
  per-layer exposure-dependent grain, **halation** (CineStill 800T red glow done right), print/scan
  transform. No one ships this accurately; we validate against Sean's real film scans.
- 💡 **Edit anything**: extend Develop beyond raws to JPEG/TIFF/HEIC (base = decoded file, same pipeline)
- 💡 **Process-version safety**: settings carry a pipeline version; old edits always re-render identically
- 💡 GPU export queue (batch exports render on the 3050 Ti via wgpu later; CPU fine now)

## Pillar 2 — Culling intelligence (Aftershoot-killer)
- ✅ Elo ranking + Bayesian taste model (unique — nobody else learns YOUR taste from YOUR duels)
- ✅ Refine/duel modes, stacks (burst/variant/crosssource), auto-advance flag flow, filmstrip at 9ms/step
- 🔨 Version stacks: RAW↔edit lineage (lane)
- 💡 **Technical quality scorer**: sharpness-on-subject (face/eye region laplacian), eyes-closed,
  blink/motion-blur, exposure-clip scoring per frame — surfaces as sortable "Quality" facet + badge
- 💡 **Auto-cull pass**: within each burst stack, auto-rank by quality+taste, pre-pick the best, mark
  soft-rejects — one keystroke to accept a whole scene (Aftershoot's core loop, but taste-aware)
- 💡 **Duplicate-shoot digest**: after import, a "cull brief" — N scenes, best-of suggestions, est. time
- 💡 Survey view (N-up compare with eliminate-on-click) and dedicated Compare view (pick vs candidate)

## Pillar 3 — The unified photo model (Renditions, spec §27)
- 💡 Photo = capture moment; RAW/exports/virtual copies are renditions of it. Grid shows photos, not files.
  V cycles renditions; old LR exports become editable bookmarks (their settings already imported).
- 💡 **Bidirectional XMP write-back**: our edits written into DNG XMP / sidecars in Adobe schema — open
  our edits IN Lightroom anytime. Zero lock-in. (We already read their schema; writing is symmetric.)
- 💡 Full-library RAW import (82k raws) as a background campaign with progress + nightly XMP re-sync

## Pillar 4 — Organization & search (beyond LR CC)
- ✅ Semantic search (3-engine RRF + captions), faces/people, map, smart collections, tags-as-facets
- 💡 Hierarchical keywording UI (manual taxonomy alongside AI tags; keyword painter for spray-tagging)
- 💡 Metadata panel: full IPTC edit (title/caption/copyright templates on import)
- 💡 Saved views (query + sort + layout as one-click workspaces)
- 💡 Timeline view (LR CC-style date river with density graph — we have date histogram data already)

## Pillar 5 — Ingest & trust
- ✅ Sources + rescan, safe trash, DB migrations with backups
- 💡 Watched folders (auto-import on change) · card import wizard with checksum + dual-destination backup
- 💡 Phone → archive: PWA share-target + auto-upload queue over Tailscale (Pixel loop closes)
- 💡 Catalog time machine: nightly DB snapshots with one-click restore, export of all edits as XMP bundle
- 💡 Integrity audit: checksums per original, bit-rot scan on the Expansion drive schedule

## Pillar 6 — Publishing & delivery (already ahead; extend the lead)
- ✅ Website publishing (drag-to-publish, frozen snapshots, review-updates), private links with passwords,
  favorites proofing, share analytics
- 🔨 Save-to-library exports join version stacks (lane)
- 💡 Client galleries v2: per-link watermarks, download sizes, pin-protected selects, "client picked" flag sync
- 💡 Export presets (web/print/insta crops) + publish pipelines (edit → auto-export → website slot)
- 💡 Print-ready exports: soft-proof + border/paper templates (the useful 10% of LR's Print module)

## Pillar 7 — Performance & polish (always-on lanes)
- ✅ 0.4s RAW preview decode, 9ms loupe nav, instant slider response, 509-test suite
- 💡 Standing perf budget CI: decode/nav/slider timings asserted in tests, regressions fail the suite
- 💡 Progressive base loading (jpg → half-bin → full-bin), speculative neighbor pregen everywhere
- 💡 Weekly polish lane: screenshot-diff sweep of every surface vs Notion-level checklist, fix on sight
- 💡 Keyboard-complete: every action reachable without mouse; printable cheat-sheet overlay (?)

## Sequencing
Wave now (running): grading/NR/defringe/auto-crop, heal, Looks+refit, version stacks, export+sync, pano.
Wave next (Fable = film engine + renditions design; lanes = Transform, Lens UI, Calibration, Detail
completion, virtual copies/snapshots/history, quality scorer, auto-cull). Then: XMP write-back, watched
folders, quality-of-life sweeps, full-library import campaign. Merge to main once the current wave lands
green; after that main rides each verified wave.
