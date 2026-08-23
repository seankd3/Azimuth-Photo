# Organizing — the design

**One sentence: photographs wear facts; you correct the facts and they learn;
albums keep what you gather.** Everything below is that sentence worn by five
nouns and one law, shaped by what Google Photos, Apple Photos, Lightroom
Classic, Capture One, Photo Mechanic, Excire, Peakto, PhotoPrism, Immich,
Mylio, and ACDSee got right and wrong (research briefs, 2026-08-22).

**Sean's 08-22 rulings, which simplified the draft (verbatim in
MASTER_PLAN 1.17):** labels are a primitive and part of smart-album
filtering; a label must be dynamically adjustable ("starbase" may mean
rockets and beach construction sites once taught); labels behave like
search terms and learn from a ✗ anywhere they appear ("search cat, 90 cats
and 1 dog — hit no on the dog and the keyword is smarter everywhere");
**the clustering suggestions are removed**, and **a smart album is simply
a saved query**. Consequences worked through below:

- **One formula unifies labels and smart albums**:
  `(derived answer ∪ pinned-in) ∖ said-no`. A label's derived answer is a
  learned concept (seed word vector + a small head fit on teachings); an
  album's is its saved query. Pins and denials are the same membership
  rows the sets primitive has always stored.
- **Vocabulary is born by teaching.** A searched term is transient until
  the first ✓/✗ lands on its results; that correction creates the label.
  The Labels list is exactly the words you have taught.
- **Deleted:** the 70-term palette auto-tagging, the k-means pair-name
  groups, the proposals shelf, the Keep verb — the push side of
  clustering. People and Sessions stay: identity and time are facts, not
  suggestions.

## The law: predicted, then earned

The ranking already runs on this law — taste predicts a score, rounds earn
it, and the info panel says which is which. Organizing gets the same law:

- **Every machine-made fact is a prediction** and wears the tilde: a label
  (`Nightscapes ~74`), a person's membership, a session boundary.
- **Every answer of yours is a decision** — append-only, with provenance —
  and turns prediction into earned fact: naming a person, confirming a
  label, removing a photo from a smart album (a removal *is* a denial).
- **Boundaries refit from decisions.** A label is seeded by a word (its
  prompt vector) or by example photographs, and a small per-label head
  learns from yes/no answers. Measured on the real library (2026-08-22): a
  concept words cannot name ("film scans of Eris", 97 truth photos in
  1,027) went from **0 of the top-20 to 9 of the top-20 after fifteen
  keystrokes** (5 yes, 10 no), median truth position halved. People learn
  the same way through ArcFace space; their thresholds are sharper because
  identity has a real boundary.
- **No knobs.** The field externalizes uncertainty as sliders (Excire's
  strictness, Peakto's tolerance) or hides it (Immich shows no confidence
  and no cutoff). We neither: calibration earns each label a threshold from
  the owner's own answers, and the tilde is the only uncertainty UI.

Why this is the bet: the research found **no trainable scene/label
classifier anywhere** — Excire states its AI "cannot be user-trained";
LrC/C1/PM have no adaptive rules ("a rules engine that accepted membership
corrections would be genuinely novel"). Only faces learn, in a few tools.
One law across labels, people, and albums is the gap in the market.

## The nouns

| Noun | What it is | LRC ancestor | The smarter part |
|---|---|---|---|
| **Folders** | where files physically are | Folders | unchanged; the pro consensus stands — storage, not organization |
| **Albums** | what you gather: plain (by hand) or smart (by rules) | Collections / Smart Collections | **exceptions**: drag into a smart album = pinned in, remove = excluded — rules ∪ pins ∖ exclusions, native. No tool has this; users have faked it with marker keywords for a decade. Exceptions also teach the labels the rules use |
| **Labels** | keywords that write themselves | Keyword List | auto-filled from the space, correctable, **learnable** (seed by word or examples, refine by Y/N). Tilde until calibrated, solid when earned — the field's blue/gray AI-vs-human segregation, worn leaner |
| **People** | who is in the photograph | People view | face wall to introduce Someones (browse their photos first, then name); merge by giving two groups one name; "not them" on any face. Corrections re-cluster (the one thing the field does learn) |
| **Sessions** | the shoots time itself declares | — (C1 Sessions are job folders; Google/Apple bury time-grouping in Memories/Trips) | capture-gap clustering, no model: the real library folds 173 dated photos into 11 sessions that auto-name legibly ("2026-03-08 · Cats · 36 photos"). Fallback to folder dates where EXIF is absent (film scans) |

**Chips are the one language under all of it.** A chip bar is an unsaved
smart album; Save as album names it. `Person: Eris · Label: Portraits ·
★3+ · 2026` is simultaneously a filter, an album rule, a Refine scope, and
a search — the same sentence in four places. Chips AND across, values OR
within, any chip negates: A-and-(B-or-C) natively, which is the shape
LrC hides behind an Alt-click in a modal.

## The surfaces

Sidebar (sections collapsible, state remembered):

    LIBRARY      All photos · Trash
    ALBUMS       plain and smart, shelved by name; ⊕ target marker on the
                 album B feeds (Quick by default, redirectable — LrC's
                 loved target-collection pattern, asked-for and missing)
    PEOPLE       named people as face-avatar rows; one row —
                 "Introduce 3 people…" — opens the face wall
    LABELS       a quiet counted list, top dozen by count, Show all;
                 tilde/solid marks prediction/earned
    FOLDERS      as today
    DRIVES       as today

- **The face wall** (a workspace stage, like Refine): big faces, click
  shows their photographs, Name on the card. Unintroduced people carry
  interim handles ("Someone 1") so browsing works before naming.
- **The grid gets session chapters** under newest-sort: quiet headers
  ("Jul 11 · Eris · 44") that jump-navigate. Sessions are also chips.
- **Search** stays two-lane and honest: cards narrow instantly (facts:
  people, labels, sessions, years, cameras, kinds), Enter always means the
  meaning search. Google shipped AI-first search and users forced a
  classic-search toggle within a year — fast facts first is a law here.
- **The info panel** answers "why is this here": the Names row (labels +
  people), and album attribution (which albums hold this photo — Apple
  Sequoia's quietly excellent addition).
- **Teach strip**: inside any label or smart album, one row of borderline
  photographs with ✓/✗ — the Refine rhythm pointed at membership. Fifteen
  keystrokes measurably reshapes a label (numbers above).
- **More like this**: on any selection — `find()` seeded by the selected
  photographs' vectors instead of words (the plumbing already accepts a
  query vector). Inside an album: its suggestions are the member-centroid's
  nearest non-members, one keystroke to admit (and admitting teaches).

## Deliberate rejections (each with a receipt)

- **No AI-first search replacement** — Google's Ask Photos backlash.
- **No confidence sliders or exposed thresholds** — Excire/Peakto knobs,
  Immich's admin-tier numbers; calibration replaces all three.
- **No one-page pile of auto-surfaces** — Apple's iOS 18 redesign backlash;
  the sidebar keeps spatial memory, sections collapse.
- **No untrainable AI keyword tree beside a user tree** — Excire's split;
  one Labels list with provenance marks instead.
- **No modal rule-editor as the primary smart-album path** — LrC's dialog;
  chips-then-save is the flow. (A rule editor may arrive later for power
  edits of saved albums.)
- **No second browse grammar for any new noun** — people, labels, sessions
  are all chips; there is one grid, one loupe, one Refine.

## Build order

1. **The rename and the calm sidebar** — Albums (was Collections), Labels,
   People sections; collapsible; empty-states that know where they are
   ("Drag photos here, press B…" inside an empty album).
2. **The face wall** + browse-before-name (server already writes interim
   handles).
3. **Smart-album exceptions** — one change in `criteria.resolve`
   (rules ∪ pinned ∖ excluded over the membership rows that already store
   true/false); drag-in and Remove become legal on smart; target-album for B.
4. **Sessions** — gap clustering + folder-date fallback; grid chapters;
   session chips and cards.
5. **The teach loop** — learned labels (word- or example-seeded), the
   Teach strip, removals-as-denials; per-label calibration when the E:
   backfill fills the space.
6. **More like this** — selection-seeded find; album suggestions.

Performance rides alongside (measured 2026-08-22 at 155k rows): the folder
tree's 30 s correlated subquery, the index spelling that costs counts and
chip totals hundreds of milliseconds, deep-scroll OFFSET, and the shelf
query that must become a written summary. Browsing stays instant when the
archive arrives.
