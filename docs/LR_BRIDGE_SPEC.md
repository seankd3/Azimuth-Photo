# Lightroom Bridge — cull anywhere, edit in LR, rank in Azimuth (frozen spec, Fable 2026-07-19)

> **Behavior reference awaiting V2 adoption.** Lightroom is a source of
> decisions to adopt, not a satellite peer. The current shape lives in
> `CORE.md` under “Alongside Lightroom.”

## Product intent (Sean)

Use LR Classic and Azimuth synergistically: cull in Azimuth (any device), edit in LR, rank exported edits in Azimuth. Picks/rejects shared both ways. LR stars tied to Elo — without corrupting the Taste↔Elo model.

## Architecture — LR is a peer through the satellite

An Azimuth Lua plugin inside LR Classic (official SDK = the only sanctioned write path into a catalog; NEVER write .lrcat directly) exchanges deltas with the LOCAL Azimuth satellite over localhost HTTP. The satellite owns identity (filepath → content_hash via its existing local-image records), clocks, offline queueing, and hub sync. No new transport; LR changes ride the existing satellite↔hub family-clock machinery with origin "lr".

## Family semantics

1. **flag (pick/reject/unflagged): bidirectional, last-writer-wins** via the existing per-family clock. Azimuth flag changes → plugin applies to LR catalog; LR flag changes → plugin pushes to satellite. Loop prevention: plugin records the (photo, value, clock) it last applied; an observed LR value equal to last-applied is an echo, not an edit.
2. **Stars: two distinct fields, never merged.**
   - `lr_rating` (exists): stars the USER set in LR → taste-prior evidence (read-time blend, per taste-elo doctrine). Never mutates Elo.
   - `elo_stars` (new, computed): hub projects Elo percentile → 0–5 stars (thresholds configurable; default 5★=top 2%, 4★=next 8%, 3★=next 20%, else unstarred; only photos with ≥N comparisons project — predicted-only Elo does not project). Small catalogs use float cutoffs as-is (a lone eligible photo is 5★; “top 2% of 40” may be a single index) — accepted coarseness, not a rounding bug. Plugin writes elo_stars INTO LR only when the photo's current LR stars == the plugin's last projection (i.e. user hasn't overridden). User-set stars always win locally and flow back as lr_rating. The projection is recomputed hub-side, delivered as part of the flag/rating delta stream.
3. **Exported edits linkage:** hub links an export in Exported Edits to its source RAW by (a) LR plugin reporting the export relationship when it observes an export job (LrExportSession source → rendered file), or (b) fallback filename-stem + capture-time matcher. Stored as an image relation ("export_of"); UI surfaces it as a stack/badge; ranking exports stays independent Elo. v2 (parked): export Elo as taste evidence for the source RAW.

## Plugin behavior

- Runs while LR is open; a background LrTask polls the satellite every ~10s for inbound deltas and watches catalog changes (catalog:addPhotoPropertyChangeObserver or periodic scan of flag/rating fields limited to the active catalog's photos changed since last clock) for outbound.
- Photos are matched by absolute filepath as LR knows it; the satellite resolves to content_hash. Unmatched paths (not yet in satellite catalog) queue and retry after the next satellite scan; surface a count in the plugin's one status line.
- Plugin UI: a single Library menu item "Azimuth Sync" showing status (connected, N synced, N pending, last error). Zero configuration beyond the satellite URL (default http://127.0.0.1:<satellite port>).
- Never blocks LR's UI; all writes via catalog:withWriteAccessDo in small batches.

## Satellite/hub API

- `POST /api/lr/deltas` (satellite): batch of {filepath, family: flag|lr_rating, value, observed_at} → satellite maps to content_hash, stamps clocks, applies locally, queues for hub — reusing the exact apply paths rating/flag sync already uses (family_clock.py; no parallel implementation).
- `GET /api/lr/deltas?since=<clock>` (satellite): outbound stream: flag changes from other devices + elo_stars projections (hub computes; satellite mirrors).
- Auth: localhost only by default (satellite ACCESS local); if satellite is remote, existing device auth applies.

## Non-goals (v1)

- No develop-settings sync in either direction (XMP bus is a separate later phase).
- No collection sync (later phase).
- No direct hub↔plugin path (satellite required — it IS the laptop peer).
- No .lrcat writes outside the SDK. No writes to catalogs other than the active one.

## Acceptance

1. Flag set in Azimuth (satellite API) appears in LR test catalog within one poll cycle; flag set in LR appears in satellite with origin lr and correct clock; no echo loops over 3 cycles (unit-test the echo ledger; live-test the loop with a scripted LrTask harness where SDK allows headless — otherwise a documented manual test script for Sean's first run).
2. elo_stars projection: hub computes from a seeded Elo distribution; only ≥N-comparison photos project; user-starred photo is never overwritten (unit).
3. lr_rating flows into the taste blend exactly as LR-import ratings do today (regression against existing tests).
4. Export observed → relation stored → visible on both images' payloads (unit + API test).
5. Full sync suites green (the family-clock contract tests must pass untouched — rating/LR-import never advances develop clocks, per closed class).

## UX layer (frozen addendum, Sean-approved 2026-07-19: "super simple, effortless UX")

Build order: after the plumbing milestones merge. Grok/GPT implement from this frozen design; no redesigns.

1. **One-click connect.** Azimuth settings offers "Connect Lightroom": the satellite writes the plugin into LR's auto-load Modules folder (per-OS path; Windows first). Next LR launch, the bridge exists. Same button disconnects (removes it). No Plug-in Manager instructions, no config screen. Detect LR installed → only then show the button.
2. **The morning collection.** The plugin maintains a dated LR collection "From Azimuth — <N> picks · <date>" containing photos picked in Azimuth since the last LR session. New picks append to today's collection; a collection whose photos are all edited/unpicked ages out (plugin removes empty ones). This is the feature's landmark moment — the edit queue is assembled before the user sits down.
3. **The ranking chip.** When newly-linked exports arrive, Azimuth desktop shows one quiet chip: "N new edits from Lightroom — rank them" → opens a focused compare session scoped to just those exports (reuse compare; scope = the new arrivals). Dismissable, never re-nags for the same batch; ignored edits just join the library.
4. **Stars need no legend.** elo_stars render identically to any stars; hovering the stars in Azimuth whispers the projection ("top 2% of your ranked photos"). A user-set star shows a subtle "yours" marker in both apps (LR side: color label or keyword is NOT acceptable — use nothing in LR; the honored value IS the marker there).
5. **Zero ceremony.** No sync windows, spinners, timestamps, or conflict dialogs anywhere. The bridge is silent while working. Sole error surface: after >24h of persistent failure, one calm line in the existing Library Health panel ("Lightroom bridge hasn't synced since <date>"). The plugin's Library-menu status dialog (plumbing milestone) remains for debugging but is never surfaced proactively.

## Global vs local excellence (frozen principle, Sean 2026-07-19)

Fields carry global truth; sets carry local excellence. elo_stars is ALWAYS the career-global percentile band — identical meaning in every view, never renormalized per scope. Within-context "best of" is expressed as set membership, never as a field: the plugin maintains an automatic "Best of <shoot>" collection per shoot (top N / top X% by Elo WITHIN the shoot; size-adaptive), reusing the morning-collection machinery. Azimuth-side, scope-sorted rating IS the local ladder; the star whisper becomes context-aware in scoped views ("Top 30% of your ranked photos · #4 in this shoot").
