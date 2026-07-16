# STORAGE_UX — one archive, no decisions (design, 2026-07-16)

## The goal (Sean)
"Never have to think about where or how my photos are stored." The destination side
already achieves this — auto-classify + fixed taxonomy, no folder pickers. The failure is
the **input/config side**: sources, watched folders, import inbox, cache dir, and library
root are surfaced as raw paths across three divergent panels with seven interchangeable
words. Fix = collapse the concept, hide the plumbing, and make the one thing the user
might wonder ("where ARE my photos?") answerable in exactly one honest place.

## The model the user should perceive
There is **one Archive**. It has **one home** (a real place on disk, shown, openable). You
feed it by pointing it at **folders of photos**; it reads them in place and organizes the
catalog by date+kind. Everything else is invisible.

Collapse the vocabulary to exactly two user words:
- **Archive** — the whole library and its home location. (replaces: library, catalog, index)
- **Folder** — a folder of photos the archive reads. (replaces: source, photo folder,
  watched folder, import inbox — these unify into one "Folders" concept with per-folder
  behavior flags, not separate models/tabs)

Internal identifiers (source, watched_folder tables, etc.) stay — this is a presentation
and information-architecture change, not a schema migration.

## Surfaces (target)
1. **One Folders list**, one renderer, one empty state, one add-flow. Kill the panel.js /
   folders.js / drawer.js / peek divergence — they call a single component. Empty-state CTA
   opens the add-flow INLINE (today both dead-end into `#system-btn`).
2. **Archive overview card** (System → Library, top): "Your archive · 147,204 photos ·
   4.6 TB · 9.9 TB free on Expansion" + the home path with an **Open** button. This is the
   single anchor that grounds every "originals stay in place, never moved" promise the app
   already makes in prose. Answers where+how-much in one glance.
3. **"Storage" heading → "Cache".** Today "Storage" shows only preview-cache tiers — actively
   misleading for someone asking where photos live. Rename; the Archive card owns real
   storage.
4. **Raw path fields demoted.** import_root / ssd_cache_dir / watched-folder path move behind
   an "Advanced" disclosure with sensible inferred defaults; the common path never shows a
   filesystem string. A folder chosen via the picker shows its **name**, path as subtext.
5. **One offline/missing voice.** Grid "Original offline", health "Disconnected", rescan and
   media errors all say the same thing with one recovery affordance ("Reconnect the drive").

## Build order (each shippable alone, lane branch, screenshot-verified)
- **A. Archive overview card + `/api/storage/overview`** (backend: archive home, originals
  count+bytes, disk free/total; frontend: the card) — highest value, additive, low blast.
- **B. Rename Storage→Cache** (trivial, removes the biggest single misconception).
- **C. Fix dead-end Add-a-source CTAs → inline add-flow.**
- **D. Unify the Folders renderer + vocabulary sweep** (the big one; do when develop quiet).
- **E. Demote raw-path config behind Advanced.**

Non-goals: no schema change, no move of any file on disk, no removal of power-user path
control (just demote it). Charter note: ui-architecture.md's noun is "Source" — this
proposes the user-facing word become "Folder"; reconcile with the charter before D.
