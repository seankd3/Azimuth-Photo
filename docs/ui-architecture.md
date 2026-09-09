# UI Architecture

This is the design charter for Azimuth Photo's interface. Every UI change should
be checkable against it. The production desktop shell is the reference
implementation of this architecture.

## The bar

- **Desktop: Lightroom Classic replacement** — culling, organizing, ranking,
  developing. A phone client is parked (`product-roadmap.md`); when it comes
  it is the same nouns and verbs in a tab-bar shell.
- Editing ships as the **Develop** module (`docs/DEVELOP_SPEC.md`): a verb on
  Photo entered with `D` that takes over the canvas full-bleed (like Loupe, a
  canvas-scale surface rather than a scrimmed overlay). Esc parks it and
  returns exactly where the user was; it owns no navigation and adds no page.

## The grammar

The interface is built from a small, closed grammar. Features are expressed in
the grammar; the grammar itself changes rarely and deliberately.

**Nouns:** Photo, Scope, Event, Person, Collection, Source.

A **Scope** is any answer to "which photos am I working with?" — all photos, a
search, a person, a collection, a source folder, a flag state, or any
combination. The app has exactly one current scope at a time.

**Verbs:** Browse, Find, Refine (rank), Curate (flag/collect), Share, Publish.

## The three-layer law

Every piece of UI is exactly one of:

1. **Shell** — permanent chrome: the scope omnibox, left panel (collections,
   library, sources, tools), right panel (info), context bar. The shell never
   grows when features are added.
2. **Lens** — a way of seeing the current scope. Today there is one, the grid
   (`web/static/v2/lens/library.js`), and Trash, People and Rank are views of
   it; Map and Suggestions are intent. A lens renders a scope or review
   surface; it does not own app navigation.
3. **Overlay** — a verb performed in place: Rank, Loupe, Export, Import.
   Overlays dim the app behind them and Esc always returns exactly where the
   user was. Verbs never navigate.

**The extensibility rule: no new pages, ever.** A new feature must land as a
new lens, a new overlay verb, a new scope type, or an annotation inside an
existing lens. If it can't, redesign the feature — not the app.

Roadmap phases expressed in the grammar:
- Sharing and publishing (parked) → verbs on Collection plus one triage lens.
- AI assistance → annotations inside existing lenses (suggested stacks in
  Grid, suggested collections in Collections, suggested keepers in Rank).
- Mobile (parked) → the same nouns and verbs in a tab-bar shell.

## Controls: the disclosure ladder

- **Rung 0 — defaults.** Right for 90% of users. A setting whose default is
  wrong for most users is a bug.
- **Rung 1 — contextual.** Controls that change what you're seeing right now
  live in the context bar (sort, Best-of, density) and nowhere else.
- **Rung 2 — panels.** Collapsible sections that remember their state.
- **Rung 3 — preferences.** Categorized and searchable; every setting carries
  a one-line plain-English consequence.
- **Rung 4 — the command palette** reaches everything above.

**One home per control.** Every control exists in exactly one place, plus a
command-palette alias. Never duplicate a control across surfaces.

## The engine made visible

Ranking quality (the sort-quality percentage and signal counts) is the app's
score. It appears ambiently wherever a scope appears — context bar, collection
cards, event headers — so "Refine" always has a visible reason. The emotional
promise of the app: your archive gets measurably better every session.

## The glass box

Local-first earns trust through transparency. What the worker is doing, and
how much is left, is always one line in the sidebar — never modal, never
interrupting.

## Speed covenant

Performance budgets are design constraints, not optimizations:

- Cull/Refine loop response: < 50 ms perceived. No spinner may ever appear
  inside a loop a photographer repeats a thousand times.
- Scope switch: skeletons within 300 ms; photos on pure black (#000); blur or
  color placeholders over gray shimmer when available.
- Grid scroll at 47k+ photos: virtualized; scrolling is the product.

## Interaction canon

Patterns adopted deliberately (and their sources):

- Zoom at click point in Loupe (Photo Mechanic).
- Ctrl/Cmd-scroll continuous grid density; semantic zoom at extremes (Apple).
- Corner-check selection model with shift-range (Google Photos).
- Undo-first: no confirmations; every action is a toast with Undo.
- Esc layers out one surface at a time; focus returns to the invoking control.
- ">" command mode in the omnibox; Ctrl/Cmd+K (Linear).
- Neutral gray chrome near photos; photos on pure black. One accent color,
  active states only. Amber strictly for offline/simulated warnings.
- Keyboard map documented in-app (?); every icon-only control has a tooltip
  with its shortcut.

## Vocabulary

User-facing words, chosen once: **Refine** (not rank/compare), **Best of**
(not top-rated), **Picked/Rejected** (not keep/toss), **Sorted %** (not
confidence/coverage), **Sources** (not folders/volumes), **Collections** (not
albums). The word "scope" is internal; users just see what they're viewing.
