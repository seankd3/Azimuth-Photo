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

Performance budgets are design constraints, not optimizations. The numbers
are the ones the craft-obsessed teams publish (Superhuman: every interaction
under 100 ms, 50 ms inside; Nielsen: 0.1 s feels instant, 1 s keeps flow,
10 s loses attention; Linear: no loading states, stale content over a
spinner):

- A keystroke in the cull loop paints its answer in the same frame, under
  50 ms, never past 100. No spinner may ever appear inside a loop a
  photographer repeats a thousand times.
- Next photograph in the loupe: paint *something* (the grid tile) under
  100 ms and sharpen in place; the decode happens behind the picture.
- Scope switch: the last answer stays until the next one lands; skeletons
  within 300 ms; photos on pure black (#000). A wait under ~200 ms shows no
  indicator at all.
- A search answers under 1 s; anything past 10 s says how far it is and can
  be stopped.
- Grid scroll at 150k photos: virtualized; scrolling is the product.
- Motion follows frequency, not taste: an act done a hundred times a day
  (a flag, a grid move, a menu) has no motion; tens a day gets 100-160 ms;
  a surface that arrives (a dialog, the loupe) 120-250 ms and never past
  300; ease-out only, transform and opacity only, never a scale from zero.
  `prefers-reduced-motion` keeps the fades and drops the movement.
- `scripts/bench.py` is the instrument for the product boundary; a
  performance row in `FINDINGS.md` ships with its before and after.

## Interaction canon

Patterns adopted deliberately (and their sources):

- Zoom at click point in Loupe (Photo Mechanic).
- Ctrl/Cmd-scroll continuous grid density; semantic zoom at extremes (Apple).
- Corner-check selection model with shift-range (Google Photos).
- Undo-first: no confirmations; every change to a set or a photograph has a
  way back. Feedback lands where the act did: a single flag or star shows on
  the tile in the same frame and its Undo waits silently on Ctrl+Z; a toast
  is for a batch or an effect off screen, 3.5 s for a notice, 8 s with a
  way back, and it retargets rather than stacks (Rauno; Apple HIG).
- Esc layers out one surface at a time; focus returns to the invoking
  control and never falls to the body. Every view change hands the keyboard
  to the stage that arrived.
- An empty state is a verb, not an illustration; the first run choreographs
  one moment (the first grid from embedded previews) before any decode.
- Rest state carries the information; hover only adds. No affordance is
  hover-only. Tooltips carry the key. A drag lifts on movement past a
  threshold, never on press; a drop target says its reason when it refuses.
- Lights Out (L) darkens the chrome to judge tone; the culling rhythm is
  decide, advance, decide, with auto-advance as the default.
- The reference set behind these, kept short: Rauno Freiberg's *Invisible
  Details of Interaction Design* and *Web Interface Guidelines*; Emil
  Kowalski's motion standards; Linear's *Why is quality so rare* and *How we
  run projects*; Superhuman's PMF engine (First Round); Nielsen's three
  response-time limits; the Lightroom Classic culling and zoom grammar
  (Lens Lounge, Kost).
- ">" command mode in the omnibox; Ctrl/Cmd+K (Linear).
- Neutral gray chrome near photos; photos on pure black. One accent color,
  active states only. Amber strictly for offline/simulated warnings.
- Keyboard map documented in-app (?); every icon-only control has a tooltip
  with its shortcut.

## Vocabulary

User-facing words, chosen once, as the shipped V2 says them: **Rank** (the
stage; a round is *chosen over*), **Best** (the sort), **Picked / Rejected**
(the cull, never keep/toss), **Albums** (plain and smart), **Labels** (taught
words), **People**, **Folders** and **Drives**, **Trash**, **photographs**
(the counted noun everywhere). The word "scope" is internal; users just see
what they are viewing.
