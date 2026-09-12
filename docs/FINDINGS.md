# Findings

What the audits found, and what became of each finding. The twin of
`MASTER_PLAN.md` §1: that ledger holds what the owner asked for, verbatim; this
one holds what a read of the product found wrong, ranked.

**The process, whole.** A finding enters with four things: the user impact in
product words, the evidence (file and line, verified at the branch's HEAD),
the concrete fix, and its rank within its surface. It leaves in one of two
ways, and only in the same commit that does the work: the status becomes
*shipped*, or *rejected* with the reason in the row. The commit that shipped
a row is the one whose subject starts with its number (`git log --grep '^P6'`),
so the ledger never carries a hash it cannot know yet. Rows are never
deleted and never renumbered. An audit runs against HEAD and lists what
earlier waves already covered, so the same finding is not found twice.

Status: `open` · `shipped` · `rejected: <why>` · `measured: <what>` when the
finding was checked and the numbers said no.

## Performance (measured on a fresh 150,000-row catalog, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| P1 | Every worker tick re-reads every page the grid ever loaded (300 calls at depth) and the drives and Trash on top; browsing stutters while tiles are made | `PageCache.refresh` re-reads only what the grid last asked for and evicts the rest; drives, Trash and size re-read only when a sweep or a lane rewrite moved them | shipped |
| P2 | A rerank holds the write lock for seconds (24 s when every row moves), so a cull key waits behind it; during a backfill it fires every five seconds | one projection helper writes only rows whose value changed, in short lock slices; the ranking follows rounds instantly and the library by the minute | shipped |
| P3 | The window opens after 11–20 s of reindexing (metadata 16 s, stacks 4 s at 150k) | the repairs run behind the first paint on the sweep lane, through the same changed-rows helper; a rebuild that changes nothing writes nothing | shipped |
| P4 | The worker's owed scan sorts the whole table per step (1.7 s) because it omits the live predicate the index carries | `i.status != 'trashed'` in `_owed_from`: 1.7 s → 0.6 ms | shipped |
| P5 | Every minute sweep bumps `swept`, so the window re-reads shelves, folders and chapters for nothing | `swept` moves only when the sweep changed something | shipped |
| P6 | Deep pages in Best sort take 889 ms since stacks collapsed: the browse indexes cannot see `stack_of` | `stack_of` appended to the three browse indexes: 889 → 20 ms; newest 62 → 23 ms | shipped |
| P7 | Each Rank round waits 1.7 s for `COUNT(DISTINCT content_hash)` | `COUNT(*)` on the browse index: 62 ms | shipped |
| P8 | Every page read probes every drive's marker on disk; an absent share costs seconds per page | the follower publishes the attached list each pass; page reads use it | shipped |
| P9 | Trash count is a table scan on every tick (71 ms) | partial index `idx_trashed`, named in the count; and asked only when something moved | shipped |
| P10 | Focusing search runs five GROUP BY scans (530 ms) on the interactive lane | the sweep lane computes facets after a changed sweep and publishes them | shipped |
| P11 | An album page re-derives membership from the log each read | measured: 18 ms for an 800-photo album; not worth a memo | measured: 18 ms |

## Grid and shell

| # | User impact | Fix | Status |
|---|---|---|---|
| G1 | Ctrl+A marks everything but the cull keys and bar stay dead until one photo is clicked | gate the verbs on `selection().length`, give the cursor a home | shipped |
| G2 | Ctrl-clicking the focused photo out of the set leaves focus outside it | focus moves to the nearest marked photo | shipped |
| G3 | Esc clears the cursor, so the next arrow jumps to the top | Esc clears the marks and the selection, keeps the index | shipped |
| G4 | Fast arrow keys drop presses at page boundaries | move the index synchronously, then ensure the page | shipped |
| G5 | Every arrow waits on a details read before it lands | details fetched from the 120 ms settle timer | shipped |
| G6 | Same as P1: every tick re-reads every loaded page | viewport-only refresh with eviction | shipped |
| G7 | Any toast kills a pending Undo | the revert stack lives apart from the toast copy | shipped |
| G8 | Row height and sort are forgotten between runs; no Ctrl+wheel density | one `remembered(key, fallback)`; wheel handler | shipped |
| G9 | A page that failed once shows skeletons forever | `refresh()` clears `failed` | shipped |
| G10 | Peeking at Trash loses scroll and selection | remember and restore on return | shipped |
| G11 | The right-click menu is not the bar's twin: no status filter, no Restore in Trash, `turn-right` named backwards | one `verbsFor(photo, view)` feeds both | shipped: Restore in Trash, the rotate button named for what it does; rejected: a status filter is a chip, not a verb, and does not belong in a photograph's menu |
| G12 | Changing sort drops the selection | `position(id, sort, view)` verb re-finds it | shipped |
| G13 | `filters.js` rebuilds the chip bar on every update | key the chips | shipped |

## Loupe

| # | User impact | Fix | Status |
|---|---|---|---|
| L1 | The edit panel opens before `state.photo` is set when the photo is not plain | set photo and blank sliders first | shipped |
| L2 | Right-click zooms | check the button in pointerdown | shipped |
| L3 | The filmstrip re-centres on every render | scroll only when rebuilt or the current changed | shipped |
| L4 | Fit clamps to the bitmap, not the photograph | fit against photo width and height | shipped |
| L5 | Previewing a look re-centres the zoom | `loupe.look(uri)` keeps tx and ty | shipped |
| L6 | D inside F opens an invisible panel | leave the clean room first | shipped |
| L7 | No Z or Space zoom toggle; the chip is inert | add both | shipped |
| L8 | At 100%, arrows re-centre | keep tx and ty across photos | shipped |
| L9 | Crop, Reset and sliders have no Undo | pass reverts to undo | shipped |
| L10 | Export toast timing and copy; the folder is not remembered | one toast, one remembered folder | shipped |
| L11 | Crop cursor and handle affordances missing | cursors and handles | shipped: the cursor says what a press would do (resize, move, draw); drawn handles are not needed with the cursor speaking |
| L12 | Copy: "arrives with its tiles", "Drag to look, release to keep", Rotate tooltip vs action | product words | shipped |

## Rank

| # | User impact | Fix | Status |
|---|---|---|---|
| R1 | A 120 ms hold after each pick; deletes feel queued | swap on click with a departing-card animation | shipped |
| R2 | RECENT=48 memory dead-ends small scopes | cap the avoid list at total − n, or drop it | shipped |
| R3 | Digits and arrows do not follow the packed order | renumber to reading order | shipped |
| R4 | No look-closer inside a round | Z or F opens the loupe on the hovered card, Esc returns | shipped |
| R5 | Progress counts coverage, not what was earned | `earned` (seen ≥ 3) plus percent sorted | shipped |
| R6 | Rerank queues behind sweeps on the scan lane; no star on Best tiles | own derive lane; star on tiles in Best sort | shipped |
| R7 | The empty state is pinned top-left | absolute, grid-centred | shipped |
| R8 | Title and progress ignore chips | use the filters' `describe` | shipped |
| R9 | Size not remembered; resizing reloads the set | remember; grow or shrink in place | shipped |
| R10 | Five mode buttons, and net/desktop/boot disagree on the default | one select, drop Close, one default | shipped |
| R11 | Search results cannot be ranked | open design; at least say why | rejected: a search is an ordering by likeness to a question, not a set of photographs; ranking inside one would rank the question. Rank draws from the scope the chips and folders make, which a search can be saved as (Save results…) and then ranked |
| R12 | `judged` and the DISTINCT total on every ask | see P7; split the read | shipped |

## Sidebar and organizing

| # | User impact | Fix | Status |
|---|---|---|---|
| S1 | An empty filtered or folder view says "Add a folder" | derive the empty copy from the view | shipped |
| S2 | Import finishing shows no toast when the panel is open; a dead Done state | always notify; delete Done | shipped |
| S3 | Empty Trash with a drive away answers in engineering voice | dry run answers by label, product copy | shipped |
| S4 | Folder and stack chips open the album editor and corrupt the chip | the editor's field table is the whole truth | shipped |
| S5 | Chips show code words (`unflagged`, `bw`); menu and editor titles disagree | one label table | shipped |
| S6 | Album Delete and Freeze have no Undo; `forget_album` vs "Delete"; plain vs fixed | one verb, a revert flag, undo toasts; refuse dropping the last import | shipped: Delete has Undo (a forgotten album is remembered by one more row in its log), the last import refuses a drop, the plain/fixed word is plain; Freeze stays one-way and says so |
| S7 | Search scope rules differ between All photos, a folder, an album | one rule | shipped |
| S8 | Recents fill with typed fragments | remember on Enter or a card only | shipped |
| S9 | Cancel vs Stop during import | the header reads Back once running | shipped |
| S10 | Forget missing has two shapes, one an "Are you sure" modal | two-click arm; delete the dialog | shipped |
| S11 | Chips cannot be removed by keyboard; × nested in a button | Backspace and Delete; real buttons | shipped |
| S12 | Five words for attaching a drive | one noun, one verb, a checkbox for the consequence | shipped: the checkbox says its consequence; the noun stays folder/drive — a drive is a root folder, and folding the drive list into the folder tree is the real unification, owed |
| S13 | `people.js` 3 s timer; `reveal_available` unused; dead `.is-refused`; teach hint with nothing selected | ship Reveal or drop it; delete the rest | shipped |

## Visual

| # | User impact | Fix | Status |
|---|---|---|---|
| V1 | `.quiet-button` inherits size and colour: three looks | its own scale: 13px, `--text-2` | shipped |
| V2 | Sidebar rows differ in size (nav 16px vs side 13px) | one row primitive, one `.is-active`, one `.count` | shipped |
| V3 | Fold tabs overlap content | position at the `--left`, `--right`, `--top` seams | shipped |
| V4 | Classic scrollbars; the rail sits beside the scrollbar | `scrollbar-width: thin`; hide the workspace scrollbar under the rail | shipped |
| V5 | A single selection wears both rings | `is-focus` only when more than one is marked; drop `.is-member.is-focus` | shipped |
| V6 | Rank empty state pinned top-left | same as R7 | shipped |
| V7 | Import stage selection language differs | the shared `is-focus` rule | shipped |
| V8 | 62 colour literals against 9 tokens; two radii | tokens: `--row-active --accent-tint --well --raised --hover-line --radius` | shipped: 62 literals → 20, all surfaces named; the reds of the danger button and the amber of a warning stay literal on purpose |
| V9 | 46px, 86px, 228px typed repeatedly | `--ctx --strip --brand` | shipped |
| V10 | Inter and Plex Mono are not shipped | self-host, weights 500 and 600 | shipped |
| V11 | `.contextbar > span { margin-right: auto }` floats the teach hint | scope to the result label and rank progress | shipped |
| V12 | Top-action spacing by whitespace | flex gap | shipped |
| V13 | The eyebrow fold is a `<p>`; + misaligned | button eyebrows | shipped |
| V14 | Dead CSS (brand-mark i, repeated `[hidden]`, month `::after` twice, loupe-close); unstyled kbd in the crop bar | delete; style | shipped |

## Documents

| # | User impact | Fix | Status |
|---|---|---|---|
| D1 | Too many documents; it is not clear which are current, accurate, or slop (owner's ask, 2026-09-09) | read every one; keep only those that own a durable fact the code cannot say, and are true at HEAD; fold or delete the rest | shipped: 9 deleted, 12 archived with dated banners, 1 folded, 14 fixed; the paths gate now reads the four docs a contributor reads |
| D2 | An empty library offered Filter, Sort, Size and Rank over nothing | the bar keeps its height and loses its controls until the first photographs arrive; the one thing on screen is the way to add them (proven on an empty home and a full one) | shipped |
| D3 | A local named like an imported helper silently shadowed it (`count`) and killed every view for an hour; fourteen other locals shadowed outer names without harm | the linter refuses a shadowed name (`no-shadow`); the kit helper is `numbered`; every shadow renamed | shipped |
| E2-3 | The sharpness pass failed on real faces ("unknown C++ exception from OpenCV"): the BGR frame handed to the landmark model was a reversed-stride view | contiguous; failed rows are dropped at repair and re-owed; six of Sean's faces now measure (eyes open, per-face sharpness) | shipped |
| D4 | A fault on the page was invisible: nothing kept it and nothing wrote it down | the window keeps every thrown handler and rejected promise and reports it to the log under the home (`report` verb); the proofs read the same list | shipped |
| D5 | A built document could pass the linter and the suite and still draw nothing | `scripts/smoke_boot.py`: a home with four photographs, the built document opened over it as the desktop does, the grid asked how many cells it drew and what it caught; part of `azimuth-check` on a desktop; the round's rule in AGENTS.md | shipped |
| D6 | The window opened whatever document was built last, so a UI edit was invisible until someone remembered `build_desktop_ui.py`, and a proof could pass on last week's bundle (the gotcha every session note carried) | `desktop.bundled_document()` builds when any source under `web/static/v2/` or the template is newer than the build (0.5 s, marked in the log as "document built"); a failed build refuses the launch and logs why; the rule leaves AGENTS.md and development.md because the machine keeps it | shipped |

## Search box and drop (second round, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| SC1 | Arrowing through the drop is hijacked by the resting mouse: scrolling a row under the pointer fires hover and the cursor jumps | hover moves the cursor only after real pointer movement | shipped |
| SC2 | The card that searches what you typed is the last of ~30 rows, below the fold | emit the everything row first when there is text | shipped |
| SC3 | The ⏎ badge sits on one row while Enter runs whatever the cursor is on | the ⏎ hint rides the cursor row | shipped |
| SC4 | The bar says 'Loading your library…' during a search | 'Searching…' when seeking | shipped |
| SC5 | Recents vanish the instant you type | recents pass through the same match filter, first | shipped |
| SC6 | A recent search cannot be forgotten | a × on each recent row | shipped |
| SC7 | The drop is invisible to assistive tech; no announced cursor | combobox/listbox roles and aria-activedescendant | shipped |
| SC8 | Folding the top bar with the drop open strands the drop | the panel toggle closes the drop | shipped |
| SC9 | More like this silently throws away chips, folders and album | keep the scope; drop only the query | shipped |
| SC10 | The like pill floats over the import workspace | hidden while importing, like its siblings | shipped |
| SC11 | Nothing shows the seeds of a likeness search; any click on the pill ends it | label opens the seeds, a real × clears | shipped |

## People wall and shelf

| # | User impact | Fix | Status |
|---|---|---|---|
| PE1 | The face wall has no keyboard | a cursor on the wall: arrows, Enter, N, Esc | shipped |
| PE2 | Naming is one verb in two grammars (right-click in the shelf, a button on the wall) | one verbsFor(person) for both | shipped |
| PE3 | Someone is detected by string prefix while settled is on the entry | pass settled from both callers | shipped |
| PE4 | Naming has no Undo | undo.show with the former name; an unname verb | shipped for a rename (Undo names them what they were); a first naming has no former name to return to |
| PE5 | One person wears three shapes across drop, shelf and wall | one .face primitive sized by a variable | shipped |
| PE6 | The wall's empty state is a black rectangle | the grid's empty state, with people's words | shipped |
| PE7 | The shelf's order is whatever the cache wrote | named first, biggest first, alphabetical | shipped |
| PE8 | The door row is the only row without a count | append the count | shipped |
| PE9 | A failed people read is silent | say it, or let the empty state say it | shipped |

## Import workspace

| # | User impact | Fix | Status |
|---|---|---|---|
| IM1 | The card is erased by default: Clear the card is pre-ticked | off by default, remembered once chosen | shipped |
| IM2 | The sticky day header paints over the context bar | top: var(--ctx), lower z-index | shipped |
| IM3 | The stage promises the grid's selection grammar and ships only the mouse half | arrows, Shift+arrows, Ctrl+A, Home/End on the stage | shipped |
| IM4 | A suspect stays faded after you tick it in | checked suspects are opaque | shipped |
| IM5 | A stopped or failed import still yanks you to Recently added | re-scope only when something came in | shipped |
| IM6 | Stop says nothing | 'Stopping…', the button disabled until it lands | shipped |
| IM7 | A running import has no Stop outside its reopened panel | the status line carries Stop while importing | shipped |
| IM8 | The card chip has no count, tooltip, shortcut or announcement | title with the key, aria-live once | shipped |
| IM9 | The day list and the day headers are the same control drawn twice | delete the panel's day list; the stage's day rows are the picker | shipped |
| IM10 | 'already imported?' asks what the library knows | say what matched | shipped |
| IM11 | The chosen kind is not remembered | recall the last answer when the guess fails | shipped |
| IM12 | An unnamed roll can become a folder named ? | fall back to the group, then Roll n; refuse illegal names | shipped |

## Timeline rail and chapters

| # | User impact | Fix | Status |
|---|---|---|---|
| TL1 | The rail eats clicks on the rightmost column of photographs | reserve a 44px gutter while the rail is up | shipped |
| TL2 | After a resize the rail lies until the next store update | render on the grid's resize frame | shipped |
| TL3 | When the day counts do not add up the rail shows and says Undated everywhere | fold emptiness into whether the rail shows | shipped |
| TL4 | January never gets a tick | no continue after the year mark | shipped |
| TL5 | The rail is unreachable from the keyboard | a slider role with arrow and page keys | shipped |
| TL6 | Nothing says which way time runs | a title, and the far endpoint year drawn | shipped |
| TL7 | Sort changes reflow the grid by a scrollbar width | no scrollbar in every sort; gutter stable | rejected: the rail's gutter and a thin scrollbar differ by design, and a change of sort re-lays the grid anyway |
| TL8 | A hovered rail shows a date label forever | hide on leave and after idle | shipped |

## Status line and pulse

| # | User impact | Fix | Status |
|---|---|---|---|
| ST1 | A drive going away is never said | an away branch above the calm one | shipped |
| ST2 | The number does not match the word: all owed kinds beside one kind's name | the kind's own count | shipped |
| ST3 | 'Up to date' flickers while work is owed | say Up to date only when the debt is zero | shipped |
| ST4 | The first run has no status line | a home-less sentence | shipped |
| ST5 | A screen reader hears the pace every two seconds | no aria-live on the ambient line | shipped |
| ST6 | Three sentences mean reading your photos | one vocabulary table | shipped |
| ST7 | The pace restarts at a third of the truth after every idle beat | seed from the instant rate | shipped |

## First run and dialogs

| # | User impact | Fix | Status |
|---|---|---|---|
| FR1 | Esc can close the app behind a modal: Rank or the wall are tested before the drive dialog | any open modal is the first rung | shipped |
| FR2 | Cancelling the folder picker leaves the drive dialog looking broken | say it, or close | shipped |
| FR3 | Every staged thumbnail decodes a raw on the bridge thread, unbounded | the thumb is made on the intake lane (one worker; the raw's embedded preview), so the window's other verbs never wait behind a card of thousands | shipped |
| FR4 | If proposing a home throws, first run is a black window | open the dialog first, fill the path after | shipped |
| FR5 | Esc on the home dialog is a silent no-op | one sentence saying why | shipped |
| FR6 | 'Point me at your photos.' is the product's only first-person sentence | 'Where are your photographs?' | shipped |
| FR7 | The stage is not virtualised; a day checkbox costs ~4,000 DOM queries | the stage keeps a map from key to cell and touches only the cells whose state moved; the day boxes are held, not queried | shipped |
| FR8 | Python exception text is the dialog's error copy | map the closed set of refusals | shipped |

## Keyboard map

| # | User impact | Fix | Status |
|---|---|---|---|
| KB1 | Tab folds panels instead of moving focus, so the sidebar and bar are unreachable | owner's call: Tab was chosen to fold, LRC's own key | shipped: resolved by W1 — Tab folds from the photographs, traverses from a chrome control |
| KB2 | There is no keyboard map in the app | a sheet generated from one SHORTCUTS table, on ? | shipped |
| KB3 | / yanks you out of Rank and runs under the loupe | gate by view; from the loupe close first | shipped |
| KB4 | Most controls have no tooltip or shortcut hint | titles from the SHORTCUTS table at boot | shipped |
| KB5 | Ctrl+A works in two of six views | route by view: import checks all | shipped |
| KB6 | Pick hides itself on click so focus falls and a second click clears | one button whose label changes | shipped |
| KB7 | Space means three things, documented nowhere | listed in the sheet; hinted on the chip and stage | shipped |
| KB8 | Y and N make a new word out of any search phrase | only a known word, or an explicit Teach | shipped |

## Details inspector

| # | User impact | Fix | Status |
|---|---|---|---|
| IN1 | No ISO, aperture, shutter or focal length | read them in tags, carry through details, one Exposure row | shipped |
| IN2 | A slider edit is described as a crop with the wrong key | cropped and adjusted said apart, D opens Develop | shipped |
| IN3 | Missing facts vanish instead of saying so | Taken, Camera, Where always speak | shipped |
| IN4 | The inspector shows one photograph while forty are marked | a set branch from the loaded rows | shipped |
| IN5 | The path is printed twice; the heading is not the filename | heading is the leaf, Folder is the prefix | shipped |
| IN6 | The Folder fact is inert | Folder, Camera and names move the view | shipped |
| IN7 | The ranking is stated twice | stars fold into the score row | shipped |
| IN8 | Place is raw coordinates | degrees with hemispheres, four decimals | shipped |
| IN9 | Nothing can be copied | click a fact to copy it | shipped |

## Two things that are one thing (second round)

| # | User impact | Fix | Status |
|---|---|---|---|
| U1 | Three answers to a heading that rides the scroll (rail, import day rows, grid bands) | one sticky-under-the-bar rule | shipped by IM2: the day rows now stick under the bar at the same var(--ctx) the rail uses; the grid's bands stay absolute by design (they are cells' own headers, not overlays) |
| U2 | Two day tallies in the import panel | one daysOf(); delete the second list (IM9) | shipped |
| U3 | Three spellings of whether a photograph is here (cell, loupe note, inspector) | one presence(photo) in kit | shipped |
| U4 | Modals escaped two ways | one open-modal rung (FR1) | shipped |
| U5 | Two dayTitle functions and two chapter walks | kit/days.js: title() and chapters() | shipped |

## Refutation of the polish pass (2026-09-09, second read)

| # | User impact | Fix | Status |
|---|---|---|---|
| X1 | All photos stopped resetting the view once Trash had been peeked at | park only from the bare library; All photos always resets | shipped |
| X2 | The cursor ring moved to a row before it arrived while the verbs still acted on the old one | the arrow run keeps its own place; ring, accent and verbs move together | shipped |
| X3 | Select All then P on unidentified photographs said nothing | a cull that changed nothing says why | shipped |
| X4 | Facets kept counting trashed photographs after a cull | a stamp of live and trashed counts remakes them | shipped |
| X5 | Folder counts and day chapters never re-read after a cull once quiet sweeps stopped bumping swept | the product says when its answer moved: cull, forget, empty, undo | shipped |
| X6 | A repair that moved stacks behind the paint was never announced | the repair says it changed the library | shipped |
| X7 | A photograph rejected before its tile was made was a grey box in Trash forever | Trash still owes its tiles, on its own small index | shipped |
| X8 | Removing a chip from the keyboard dropped focus to the body | focus lands on the chip that took its place | shipped |
| X9 | The Undo button hid while its revert was still live | a notice without a way back leaves the button | shipped |
| X10 | The bundler died on a traceback when the fonts were not installed | it says to run npm ci | shipped |
| X11 | The loupe's fit ceiling mixed axes for a turned photograph | matching axes | shipped |
| X12 | A pick that waited on a fill could write onto an undone set | the generation is checked after the wait | shipped |
| X13 | An unreadable date decision churned a good metadata cache row every repair | a bad decision raises; only a bad row is dropped | shipped |

## Stacks (owner's ask, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| K1 | Cadence stacks over-group and hide frames; every stack is collapsed by default | a stack is a decision (S makes one from marked frames, or from the burst the cadence law proposes around one frame; Shift+S unstacks; Undo on both); stacks open by default with a band; the badge folds one, a bar toggle collapses all | shipped |

## Appearance (owner's ask, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| A1 | Every tile wore a hairline; on black it read as a wire fence | no stroke on a photograph: the hand's ring and the mark's ring are the only rings; stacks wear a band | shipped |
| A2 | Seven type sizes and three kinds of caps | four steps as tokens (label 11 · meta 12 · body 13 · title 15); eyebrows on the label step | shipped |
| A3 | An empty inspector column said "Select a photo" | the library at a glance: photographs, starred, Trash, drives, what the worker is doing | shipped |
| A4 | Unicode glyphs stood in for icons and rendered unevenly | one drawn 16px set (kit/icons.js) for albums, smart, labels, people, stacks, cameras, calendar, clock, recent, shapes | shipped |
| A5 | Day chapters were tiny caps mono, easy to miss | the day in Inter 12 medium, its count beside it quieter in mono | shipped |
| A6 | Two counts disagreed (title vs sidebar) with no explanation | "121 photos · 3 behind covers" whenever frames wait behind covers | shipped |
| A7 | Tiles popped in as they loaded | a 160 ms fade on arrival; reduced motion honoured | shipped |
| A8 | Rows of five at 220px left a lot of black | default density 180, remembered once changed | shipped |

## Feel (third round, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| F1 | A day's last row is left short, so on small daily shoots half the width is black and sizes jump day to day | justify a chapter-closed or final row up to 1.6× the target height | shipped |
| F2 | A stack's band is a ring per cell: four boxes, not one set | one band drawn once behind each row segment of the stack | shipped |
| F3 | A marked frame inside a stack wears two rings; the accent vanishes over an orange photograph | one ring inside a band; a dark companion under the accent | shipped |
| F4 | Photographs say they are not clickable (cursor: default) | pointer; grab while dragging | shipped |
| F5 | The day header is inert | a button that marks the day's frames; Shift extends | shipped |
| F6 | The stack badge is mouse-only and invalid markup; the cover trails its members in newest sort | tabindex and keys on the badge; the band says the set wherever the cover sits | shipped |
| F7 | Ctrl+wheel outside the library zooms the whole app; inside it steps in 17 notches | preventDefault first; continuous by delta; the layout on the next frame | shipped |
| F8 | Stars are spelled four ways | one spelling: the text star; the unused drawn star leaves the set | shipped |
| F9 | Sidebar label edges zig-zag: no reserved icon slot | one 18px slot on every row; Library rows get their marks | shipped |
| F10 | FOLDERS and DRIVES show a heading over a void; the button that fills them is across the window | an empty section shows one quiet row that fills it | shipped |
| F11 | Nothing says a section folds until it is folded | a drawn chevron always, rotated by state | shipped |
| F12 | The active row's accent bar pokes out of its rounded corner | a rounded bar inside the row | shipped |
| F13 | Fold memory keyed on the English word; the door row mixes units | data-section ids; '2 people' | shipped |
| F14 | Four surfaces say the same count at once | the calm status line says Up to date, without the number | shipped |
| F15 | The bar's controls sit 1000px from the photographs | keep as designed | rejected: the verbs sit left with what they act on, the view controls right, as in Lightroom; the resting bar is empty by design |
| F16 | The top fold tab is drawn over the context bar | a 12px handle on the seam | shipped |
| F17 | Five control idioms in one bar; the range track is the brightest thing in the window | the range styled to the palette; Rank at the end | shipped |
| F18 | 'Stacks open' states a status, not an action; shown with no stacks | an action label with aria-pressed; shown only when a stack is in view | shipped |
| F19 | Searching makes Sort vanish | kept, disabled, with the reason | shipped |
| F20 | The rail's first year and month print on top of each other | one placement clock for both | shipped |
| F21 | A one-year library reads 2026 … 2026, half cut off | the end year only when it differs, inside the track | shipped |
| F22 | The right fold tab covers the rail | the tab steps aside by the rail's width while the rail is up | shipped |
| F23 | The fold tabs are hover-gated at 55% opacity | rest visible; hover lifts the ground only | shipped |
| F24 | The loupe hard-cuts in | a 120 ms fade and settle, reduced motion honoured | shipped |
| F25 | Reopening the loupe flashes an empty strip | the strip's DOM survives a close | shipped |
| F26 | Esc in the search box blurs to nowhere | clear and keep focus; a further Esc hands focus to the grid | shipped |
| F27 | Esc never reaches the ladder from a focused empty box | propagation stops only when the drop was open or the box had text | shipped |
| F28 | Chromium's own × in the search pill | hidden; the app's clear is the one clear | shipped |
| F29 | The toast paints under menus, the drop and dialogs | above every non-modal layer | shipped |
| F30 | One 8-second clock for a notice and an undo alike; no way to dismiss | 3.5 s for a notice, 8 s with a way back; Esc's first rung hides a toast | shipped |
| F31 | The toast is off the palette and asks for a weight the fonts do not ship | named surfaces; weight 600 | shipped |
| F32 | The toast centres on the window, not the photographs | centred on the workspace | shipped |
| F33 | The glance suppresses zero rows and keeps a leftover sentence | None is said; the ? is a key cap; the sentence goes | shipped |
| F34 | Exposure and Lens vanish when unread; Folder vanishes on a flat drive | Unknown for Exposure and Lens; the drive's root as the folder | shipped |
| F35 | Label and value 400px apart | a tight label column, values left-aligned | shipped |
| F36 | Copy is mouse-only and copies app vocabulary | values are buttons; vocabulary rows are not copied | shipped |
| F37 | The inspector is rebuilt on every store beat | keyed on what it shows | shipped |
| F38 | The drawn set is worn almost only in the drop; an unknown name draws the wrong glyph | chevrons and the stack chip wear the set; an unknown name throws | shipped |
| F39 | 85 raw font sizes remain beside the four tokens | swept onto the scale; two display tokens for the dialogs | shipped |
| F40 | Icons render at 14 on a 16 grid; filled marks drawn as outlines | 16px; smart fills | shipped |
| F41 | Esc un-narrows chips but not an album, a folder, or Trash | one rung un-narrows the scope one step | shipped |
| F42 | Two key tables say four facts twice | one table: the sheet and every tooltip read it | shipped |
| F43 | The sheet lacks the mouse grammar, chip keys and rail keys; one row renders as one wide cap | rows added; caps split on commas too | shipped |
| U6 | Two key tables | one (F42) | shipped |
| U7 | A chapter joined with ' · ' and split again on it | the break carries title and count apart | shipped |
| U8 | Four spellings of a star | one (F8) | shipped |
| U9 | Two spellings of a stack in one bar | the chip says 'In this stack' without a glyph | shipped |
| U10 | Two policies for an empty sidebar section | one: a quiet row that fills it (F10) | shipped |
| U11 | Two byte-identical row rules | deleted | shipped |
| U12 | Two chevrons drawn as text | the drawn chevron (F11, F38) | shipped |
| U13 | Two cursors for 'cannot press' | one | shipped |
| U14 | One verb, three names (Add folder… / add-drive / add a folder) | add-folder everywhere | shipped |
| F44 | The mark's ring and the hover line have been invisible since A1: an inset shadow on the cell paints under its image | the rings are drawn on a veil above the picture | shipped |

## Feel (fourth round, 2026-09-09)

| # | User impact | Fix | Status |
|---|---|---|---|
| Q1 | Empty Trash cannot be confirmed as shown: the dialog prints 12,043 and compares against 12043 | compare on digits; say when the number is wrong | shipped |
| Q2 | First run pops the OS folder chooser with no warning, again on every navigation | never auto-open the picker; the empty state carries the promise and the button | shipped |
| Q3 | In Import, Enter or Space on a focused button starts the import instead of pressing the button | the workspace keys yield to a focused control | shipped |
| Q4 | In Crop, Enter on the focused Cancel button applies the crop | Enter on the crop bar presses the button | shipped |
| Q5 | In Rank, X rejects the card under the mouse, not the keyboard cursor | the cursor wins when it exists; hover answers only without one | shipped |
| Q6 | Dropping 400 photographs into the wrong album cannot be taken back | Undo on add-to-album | shipped |
| Q7 | Nudging a Develop slider with arrows writes a decision and a toast per key press | keyboard changes coalesce into one decision | shipped |
| Q8 | Esc and D do nothing while a Develop slider has focus | range and checkbox inputs are not typing | shipped |
| Q9 | The pick in Rank is never seen | the winner wears is-picked for a beat before the swap | shipped |
| Q10 | Rank's cursor starts nowhere, then lands on a stranger after a mouse pick | cursor starts at 0; a mouse pick leaves the cursor off the fresh slot | shipped |
| Q11 | The toast offers Undo for things that cannot be undone | the button starts hidden | shipped |
| Q12 | Right-click menus are mouse-only and fall off the screen near an edge | one placed menu: clamped, focusable, arrows, Shift+F10 | shipped |
| Q13 | Trash offers album verbs and a key that refuses to work there | no album rows in Trash | shipped |
| Q14 | A person can only be named by right-clicking | a Name… control on the row; N on a focused row | shipped |
| Q15 | In Import, Shift+Arrow after a click selects the wrong range | the anchor is a key everywhere | shipped |
| Q16 | Up and Down in Import and on the People wall land on the wrong card | columns measured from the cells, not guessed | shipped |
| Q17 | The filmstrip is 29 tab stops and an arrow throws focus to the body | roving tabindex; focus survives a rebuild; option roles | shipped |
| Q18 | The filmstrip glides under reduced motion | smooth only when motion is welcome | shipped |
| Q19 | Enter on the first-run dialog can settle the home at … | submit disabled until the path is real | shipped |
| Q20 | The crop rectangle cannot be touched from the keyboard | arrows nudge, Alt+arrows an edge, 0 full frame; on the sheet | shipped |
| Q21 | A twitch of the mouse writes a sliver crop | a drawn box under the floor is dropped; apply refuses a degenerate box | shipped |
| Q22 | Tabbing through a card import means thousands of checkbox stops | cell boxes leave the tab order; the stage is one stop | shipped |
| Q23 | The People wall has two cursors that never agree | the card is the focusable thing | shipped |
| Q24 | Rank's two settings are two kinds of control and neither has a key | one segmented shape; [ ] step the size, M cycles the mode; on the sheet | shipped |
| Q25 | Synchronize reports zeros | empty parts dropped; nothing changed said plainly | shipped |
| Q26 | Album messages count nothing in particular | photographs named, no bookkeeping words | shipped |
| Q27 | Looking at a card talks through the toast every 600 ms | progress goes to the status line | shipped |
| Q28 | The Import button says Cancel and its tooltip says Back | the title follows the label | shipped |
| Q29 | No faces yet is drawn across the whole window | the wall is a positioned box | shipped |
| Q30 | Removing a chip cannot be undone; Esc from the editor drops focus | Undo on chip removal; focus returns to the chip | shipped |
| Q31 | Dead stylesheet for the destinations list | deleted | shipped |
| Q32 | Dead key handler in the edit panel | deleted | shipped |
| Q33 | The zoom chip says where you are, not what pressing it does | the chip names the act, shows the state and its key | shipped |
| Q34 | Full frame looks like it removed the crop; nothing is written until Apply | Remove crop applies at once | shipped |
| U15 | Close marks drawn from the set vs typed × | drawn everywhere | shipped |
| U16 | The cull verbs implemented twice (cull.js, rank.js) | one path: the rank stage's verbs through cull.apply with an after-hook for the seat swap | shipped |
| U17 | Pick means the cull flag and the winner of a round | the round's word is chosen | shipped |
| U18 | photographs vs photos for one count | photographs | shipped |
| U19 | The sidecar act reported two ways | one sentence | shipped |
| U20 | Reset in Develop vs Full frame in Crop | Remove crop (Q34); Reset stays for a recipe | shipped |
| U21 | inspector-facts hidden has two owners | one owner | shipped |
| U22 | Popovers clamp to the viewport; context menus do not | one placer (Q12) | shipped |
| U23 | Two empty-state idioms | emptyState() for Rank too | shipped |
| U24 | Go to this person is the whole row on the shelf, only the face on the wall | the whole card | shipped |
| U25 | One anchor variable holds an index and a key | a key (Q15) | shipped |
| U26 | Two cursors on the face wall | one (Q23) | shipped |
| U27 | One import reports progress in two places | the status line (Q27) | shipped |
| U28 | The column count derived three ways | measured (Q16) | shipped |

## Refutation (feel round)

| # | User impact | Fix | Status |
|---|---|---|---|
| X14 | In Best order every starred photograph wore a black veil: the ring veil and the star chip shared ::after | the veil is a real child element | shipped |
| X15 | On an away or unshowable cell the mark's ring shrank to the 22px circle | same: the veil is its own element | shipped |
| X16 | Sidebar counts stopped sitting flush right once an icon came first; Add a folder… rendered in the count style | row rules name what they style, not where it sits | shipped |
| X17 | The stacks button could vanish while collapsed, with no way back | kept whenever collapsed | shipped |
| X18 | A plain notice ate Esc for 3.5 s | the toast takes Esc only with a way back | shipped |
| X19 | Marking a day pinned every page of that day into the refresh set | a read that is not a look leaves the refresh memory alone | shipped |
| X20 | markRange could detail a null cursor | guarded | shipped |
| X21 | A crowded year was never named on the rail | named at its next chapter with room | shipped |
| X22 | The clamped first year could be overprinted | the mark records where it sits | shipped |
| X23 | The filmstrip opened uncentred: laid out while the stage was hidden | laid out after the stage is shown | shipped |
| X25 | MASTER_PLAN recorded the old status line | updated | shipped |
| X24 | An unknown icon name at boot is a blank window | keep as designed: a typo in a static name is a build-time fault the throw surfaces at once | rejected: fail loud beats a wrong glyph |
| X26 | Enter on the wall's Name… button browsed the person | a focused control keeps Enter and Space on every stage | shipped |
| X27 | Enter or Space on a Rank size or mode button cast a vote | same guard in the Rank branch | shipped |
| X28 | A pick queued during an abandoned beat fired on the next pick | the queue is dropped with the beat and on load | shipped |
| X29 | A queued mouse pick replayed as a keyboard pick | the queue carries how the pick was made | shipped |
| X30 | Two context menus could open at once; the opener went stale and focus fell to the body | one menu at a time; the opener remembered per menu; every hide through the one door | shipped |
| X31 | Shift+F10 in a text field killed cut, copy and paste | text fields keep their own menu; the key is taken only when a menu answered | shipped |
| X32 | Esc from the chip editor could focus nothing (a precedence slip) | the chip, else the + button | shipped |
| X33 | Import's rows were measured from its newest day, not its grid | the grid template's column count | shipped |
| X34 | Letting go of a Develop slider kept it 400 ms late | the release flushes after the change it causes | shipped |
| X35 | Rank, Crop and Develop could not be driven in the harness | stubs for rank, round, the cull verbs and develop_state | shipped |

## Feel (fifth round, 2026-09-10)

| # | User impact | Fix | Status |
|---|---|---|---|
| W1 | Tab folds panels from everywhere, so no chrome control can be reached by keyboard | Tab folds from the photographs and the body; from a chrome control it traverses (resolves KB1) | shipped |
| W2 | Folder rows are divs: unreachable, inaudible, no tree semantics | buttons in a tree: roving tabindex, arrows, Left/Right fold, Home/End, aria-expanded | shipped |
| W3 | The search drop steals Enter when the mouse rests over it | a pointerover counts only after a real pointermove since the last render | shipped |
| W4 | First run: Enter opens the OS picker (focus lands on Change…) | focus the dialog until the proposal lands, then the submit | shipped |
| W5 | The export dialog opens on its close button, so Enter throws it away | focus the submit | shipped |
| W6 | Export says Exporting before the destination is chosen, then goes silent | nothing until chosen; progress on the status line; the outcome on the toast | shipped |
| W7 | Opening the loupe, Rank, People or Import drops the keyboard on the body | each stage focuses its own first thing | shipped |
| W8 | A marked set is invisible to a screen reader | aria-pressed from the marks; aria-current on the cursor | shipped |
| W9 | The name popover is an unlabelled field you can Tab out of | role dialog, labelled, two-stop focus | shipped |
| W10 | The first outside click never dismisses a popover opened from the topbar | compare against the opening event, not a flag | shipped |
| W11 | A duplicate album name is discovered after the popover closed and the text is gone | validated in the popover against the albums on hand | shipped |
| W12 | Renaming an album says nothing unless it had children | always said | shipped |
| W13 | Recent searches can only be forgotten with a mouse | Delete on a Recent row forgets it | shipped |
| W14 | Labels have no verbs: a taught word can never be renamed or forgotten | the same menu grammar: Rename…, Forget this word | shipped |
| W15 | Browsing a label or a person leaves no sidebar row marked | marked from the worn chip | shipped |
| W16 | The ~ before counts is never explained | one clause in the row title | shipped |
| W17 | The safety dot has no legend | a title per state; a line on the sheet | shipped |
| W18 | A refused drop looks like an accepted one and never says why | refusal in warn with the reason | shipped |
| W19 | Only album rows accept a drop; others refuse silently | people and labels accept (they are chips); folders refuse with a word | shipped |
| W20 | Dragging files from Explorer lights an album as accepting, then nothing | highlight only for the app's own drag | shipped |
| W21 | The loupe cannot be panned from the keyboard | arrows pan while zoomed; Home recentres | shipped |
| W22 | Double-click in the loupe zooms in and straight back out | the paired click is swallowed | shipped |
| W23 | The zoom chip reads Z 100% to a screen reader | aria-label kept in step | shipped |
| W24 | The loupe's trouble note is silent | role status | shipped |
| W25 | The status line and result label are never announced | role status on both | shipped |
| W26 | 2 drives away · here when it is | when they are | shipped |
| W27 | Mapping the space is app vocabulary; one status string has a period | Learning what your photographs look like; no period | shipped |
| W28 | The timeline's date label is mouse-only | the keyboard scrub says the day | shipped |
| W29 | The timeline slider is announced horizontal | aria-orientation vertical, the keys in the label | shipped |
| W30 | Every failure speaks the bridge's words | kit/why.js: known phrases mapped, otherwise what the person was doing | shipped |
| W31 | The export size chooser has no group or pressed state | role group, aria-pressed | shipped |
| W32 | The Add-folder dialog announces itself as the folder's name | aria-label names the act | shipped |
| W33 | Import… keeps its label while an import runs | Importing… with a title that says it is the way back | shipped |
| W34 | The drop never tells a screen reader what Enter will do while typing | the everything row is the active descendant by default | shipped |
| W35 | The drop's listbox holds untyped headings and wrappers | groups and presentation roles; the cap is aria-hidden | shipped |
| W36 | Seven caps on seven offer sections; one uncapped | one cap rule | shipped |
| W37 | Toast strings drift in number, tense and punctuation | one house style; the eight rewritten | shipped |
| W38 | Context menus are lists, not menus | menu, menuitem, separator roles | shipped |
| W39 | The card chip carries aria-live on a hidden button | dropped; the status line announces | shipped |
| W40 | The grid is N tab stops | roving tabindex on the cursor | shipped |
| U29 | aria-pressed vs aria-selected for the chosen photograph | pressed on buttons, selected in listboxes — one rule written down | shipped: written down in AGENTS.md — pressed on buttons, selected in listboxes and trees |
| U30 | Work in flight on the status line vs the toast | the status line (W6) | shipped |
| U31 | photos / frames / photographs | photographs | shipped |
| U32 | Colon, em dash, semicolon as the outcome joiner | one joiner | shipped |
| U33 | Three names for the sidecar act | one | shipped |
| U34 | The comes-back sentence written twice | once | shipped |
| U35 | Two spellings of already-imported in one sentence | one | shipped |
| U36 | Two ways to run a search from the box, one remembers | one | shipped |
| U37 | Two measurements of the same rail point | one | shipped |
| U38 | Two names for All photos | one | shipped |
| U39 | presence() punctuates three ways | one | shipped |
| U40 | Two number formats in one file | toLocaleString | shipped |
| U41 | Two dialog focus policies | aim at the act (W4, W5) | shipped |
| U42 | Two policies for marking the current view in the sidebar | one (W15) | shipped |

## Performance (fifth round, 2026-09-10, measured on a 150k copy)

| # | User impact | Fix | Status |
|---|---|---|---|
| P12 | Scroll stutter inside a folder or with a camera/stars chip: 70–1,100 ms per page (unindexable substr scope, row fetch per walked entry) | folder scope as a tail range on idx_photos_tail (page 1,094→9 ms) | shipped |
| P13 | S lags 650 ms and holds the write lock: stack/unstack re-project the whole library | project only the touched identities on stack/unstack | shipped: stack/unstack project only what they touched (666→0.5 ms) |
| P14 | size(), days() and the rank total table-scan: 150–430 ms per view change | INDEXED BY the browse index; rank total once per scope | shipped: size 93→5 ms, days 146→73 ms on the browse index |
| P15 | position() numbers the whole scope: 156–340 ms per sort change | keyset count on the sort's index | shipped: a counted range, 156–341→21–29 ms; every sort now ends in the id |
| P16 | Chores burn 260–1,000 ms of queries per step and repeat every 5 s with the archive away | skip the trash half for keyed passes; stop probing after the first locate miss | shipped: keyed passes skip Trash; a step stops after 8 located-nowhere heads |
| P17 | Page cache 2 MB, mmap off on a 130 MB catalog: every scan 1.5–5× slower | mmap 256 MB in connect() | shipped: mmap 256 MB |
| P18 | rerank writes by content_hash, 0.8 s lock per slice | UPDATE by id; quantize elo; slice 500 | shipped: by id, elo rounded to a tenth, slices of 500 |
| P19 | facets recompute 1 s on the UI lane after any cull | computed on the sweep lane; the UI only reads | shipped: remade on the sweep lane; the window reads the last answer meanwhile |
| P20 | boot.repair is 5.7 s of CPU behind first paint | one-shot residue repairs gated by a decision row; metadata reindex only when its rows moved | shipped: residue rules run once per catalog; metadata reindex only when a newer answer exists |
| P21 | Lexical search walks the library per keystroke (100–165 ms) | LIKE on tail only; camera/lens/date terms resolve to chips | measured: mmap halves it; resolving camera/lens/date words to chips is a search redesign, parked |
| P22 | albums() runs a windowed COUNT per album | sets.counts() one pass | measured: 117 ms for 21 albums; the row shows the union-and-library count, which one pass over decisions cannot say; revisit past ~100 albums |
| P23 | 423 B per page row; the four presence booleans are folded into one word anyway | measured: low priority, left | measured: left as is |

## Craft (from the study of Notion, Linear, Superhuman, Raycast and Lightroom, 2026-09-10)

| # | User impact | Fix | Status |
|---|---|---|---|
| N1 | A single flag or turn raised a toast for what the tile already showed | the way back waits silently on Ctrl+Z; a batch, a reject or a word about the unidentified still speaks | shipped |
| N2 | No way to darken the chrome to judge tone | Lights Out on L | shipped |
| N3 | A sharpness run at 100% must survive the arrows | measured: already so — the loupe keeps scale and position across frames | measured: already so |
| N4 | A press is not felt | a 3% dip for the moment a button is down, transform only, 100 ms | shipped |
| D2 | ui-architecture.md's vocabulary named Refine, Collections and Sources; the app says Rank, Albums, Folders | the doctrine says what the app says, and carries the study's numbers | shipped |
| N5 | A wait under 200 ms flashed Loading… over the last answer | the word appears only for a wait a person would notice | shipped |
| N6 | The loupe faded in on every open, an act done many times an hour | instant, by the frequency rule; one easing curve token for what does move | shipped |
| N7 | No Lightroom letters for the two most-used moves | E opens the loupe, G is the grid from any stage | shipped |
| N8 | The filmstrip recentred on every arrow | it stays put while the current frame is in view | shipped |
| N9 | The loupe never taught its keys | a hint under the picture on the first three opens | shipped |
| N10 | A toast under the pointer expired while being read | the clock waits under the hand | shipped |
| N11 | Text could be selected across the photographs | no selection on the stages | shipped |
| N12 | Counts beside each other jittered as digits changed | tabular figures on every count | shipped |
| H1 | A harness stub that was missing answered null and passed | it throws | shipped |
| N13 | A burst could not be judged as a round of its own | N surveys the marked frames in Rank, sized to the burst; the survey ends with the sitting | shipped |
| N14 | No sharpness read on the eyes without a hand on the mouse | . puts the next face at 100% under the centre (a faces verb over the face pass) | shipped |
| N15 | No photos here yet | photographs (U31) | shipped |
| X36 | Tab from a context-bar button folded the panels: the bar sits inside the workspace | Tab folds only from the stages themselves | shipped |
| X37 | Right or Left in the folder tree threw the cursor to the top; two tab stops | the cursor's row survives a rebuild; one stop | shipped |
| X38 | A silent single-frame way back left a stale batch toast whose Undo rebound | keep() puts any standing toast away | shipped |
| X39 | One failed facets remake froze the filter offers for the process | the flag clears in finally; the failure is logged | shipped |
| X40 | Eight away heads stopped a chores step before identity work and other drives | misses are counted per root; the walk goes on | shipped |
| X41 | A refused drop on a folder row was invisible | folder rows wear the drop styles | shipped |
| X42 | L toggled Lights Out from any focused row, Shift and Alt included | a plain L from the library or the loupe, not on a control | shipped |
| X43 | Renaming a label to a taken word said album | the popover names its noun | shipped |
| X44 | rename_label would raise before a home was chosen | the rank nudge waits for a product | shipped |
| X45 | outside() kept the substr scan and a second spelling of under-this-folder | not_of(folder(path)) | shipped |
| X46 | position()'s docstring claimed an index seek | says the one counted pass it is | shipped |

## Rank, from the owner's sitting (2026-09-10)

| # | User impact | Fix | Status |
|---|---|---|---|
| RK1 | Learn dealt the bottom of the library: among equally unsure windows the lowest-predicted came first, and finding rounds waited for 95% coverage | ties go to the highest rated; finding rounds are half the sitting once there are leaders (2n judged) | shipped |
| RK2 | Diverse dealt one afternoon: the pool was one window of ids, one or two shoots | eight windows across the scope; without vectors, one frame per day in turn | shipped |
| J1 | No journeys to read a round's feel against the last | scripts/journeys/*.js, six probes to a gallery | shipped |
| M1 | The monthly numbers: repairs 7% of September's commits by subject (August 2.7%); app.js touched by 27 of 55 commits | measured; app.js is the assembly point — a split by surface is the next shape decision | measured |
| O1 | No second pass: after rejecting, seeing only the unflagged or only the picked took the filter menu | V cycles the pass: everything, the unflagged, the picked | shipped |
| O2 | Freezing an album was one-way | the rules ride back; Undo redefines the album | shipped |
| O3 | A first naming had no way back | the name is taken back and the face is a Someone again | shipped |
| O4 | The bench had no budgets, so a regression was a number nobody compared | budgets per verb; --budget fails the run | shipped |
| S12 | Fold the drive list into the folder tree | rejected: the tree is one tree over every drive by design (library.folder_tree); a drive is a property of its nodes, and the Drives section carries the drive's own facts | rejected: by design |

## Personas (sixth round, 2026-09-10): keyboard, screen reader, 13-inch, 4K

| # | User impact | Fix | Status |
|---|---|---|---|
| K1 | No key moves focus from the photographs into the chrome | F6 cycles bar → sidebar → inspector → topbar; on the sheet | shipped |
| K2 | A letter typed on a focused chrome button fires a photo verb | the control guard hoisted before the verb ladder | shipped |
| K3 | Enter or Space on a focused chrome button opens the loupe | same guard | shipped |
| K4 | Arrows yank focus from the chrome back to the grid | same guard | shipped |
| K5 | The Develop panel is unreachable by keyboard | D focuses the first slider; Esc and D return to the loupe | shipped |
| K6 | Tab during an import hides the import panel and drops focus | the fold refuses while a panel owns the focus | shipped |
| K7 | Esc out of the + Filter menu drops focus to the body | the chip menu opens through showMenu | shipped |
| K8 | Rank loses the keyboard on every pick | the selected card is focused after render | shipped |
| K9 | Export, New album and Add folder have no key and no menu entry | a leading > in the search box lists every enabled verb on the screen by its tooltip, with its key; typing narrows, Enter presses the first, the sheet teaches > | shipped |
| K10 | Every stack badge is a permanent tab stop | the badge roves with its cell | shipped |
| SR1 | The status line speaks the worker's pace every two seconds | announce state changes only; the pace is aria-hidden | shipped |
| SR2 | A cull, a pick and a view change announce nothing | one polite live region every act writes one sentence to | shipped |
| SR3 | The toast's text is set while hidden, so it is often not announced | text after visible; the toast stays in the tree | shipped |
| SR4 | The stack badge is a control nested in a control | the badge leaves the button; the count is in the cell's name | shipped |
| SR5 | Drive rows are inert divs; Re-scan is mouse-only | buttons in the row primitive; the state in the name | shipped |
| SR6 | Chip field menu items have no menuitem role | the role on the item builder | shipped |
| SR7 | The chip editor and the likeness popover are unlabelled boxes | role dialog, labelled by their eyebrow | shipped |
| SR8 | The People wall card is a focusable div wrapping two buttons | the picture button roves; the card is a container | shipped |
| SR9 | Stars are announced to nobody | the stars in the cell's name | shipped |
| SR10 | The safety dot and the drop's dismiss are silent or invalid | role img with a label; the dismiss out of the listbox | shipped |
| LP1 | 40% of a 1280 px window is chrome; no breakpoint above 1050 | a middle rung at 1440 | shipped |
| LP2 | Three photographs per row on a 13-inch at the default density | the default seeded from the grid's width | shipped |
| LP3 | The context bar overflows at 1280 and scrolls the workspace sideways | the bar scrolls on its own axis | shipped |
| LP4 | A long search phrase collapses the search box to zero | min-width on the search column; ellipsis on the title | shipped |
| LP5 | The keys sheet is two columns inside a vertical scroller at 800 px | one column below 900 px of height | shipped |
| LP6 | Rank at 9 wastes a quarter of the stage; 12 buys smaller cards | rejected: stretching one shelf gives its cards more area than the rest, and equal area is the law a round is honest by (a size bias would be written into durable ranking data); the leftover is the price of it, and [ ] change how many are dealt | rejected |
| LP7 | A 237 px card is not a judgement | sizes below the floor are offered with the reason, disabled | shipped |
| LP8 | The filmstrip eats 11% of an 800 px window | the strip scales with the height | shipped |
| LP9 | The export dialog cannot shrink and its size group is a 2×2 | one dialog width rule; a single-row size group | shipped |
| LP10 | A popover can run off the bottom at 768 | height clamped to the space at the chosen side | shipped: one absolute ceiling (HK6); per-side clamp folded in |
| HK1 | The density slider tops out at 320 on 4K | the range driven from the grid's width | shipped |
| HK2 | The filmstrip is 29 fixed cells, left-aligned in a 4K void | sized from the strip's width; centred when it underfills | shipped |
| HK3 | An edited loupe is a 2048 px preview upscaled and called 100% | an edited photograph's loupe stops at the preview's own pixels (no native ceiling to upscale to) and the chip says Preview, never a 100% it cannot deliver | shipped |
| HK4 | Panel widths are absolute: a ribbon at 4K | clamped panel widths | shipped |
| HK5 | The People wall is a field of 164 px cards at 4K | card width clamped to the viewport | shipped |
| HK6 | Four viewport rules for popover heights; two unbounded at 2160 | one rule with an absolute ceiling | shipped |
| HK7 | The rail stays 44 px on a 2,000 px screen | track and label step scale with the rail's height | shipped |
| HK8 | The page is smaller than one 4K viewport at the densest setting | page size from the measured viewport | shipped |
| HK9 | Rank stops at 12 where the screen could hold more | 16 and 20 offered when the card clears the floor | shipped |
| HK10 | The verbs sit 3,300 px from the view controls at 4K | the bar's content capped at 1600 px, centred | shipped |
| RK3 | Tournament felt laggy between a click and the next set | the 120 ms beat before the swap removed; past a pair the cards use the 1,024 px grid tile, not the 4,096 px loupe | shipped |

## Personas (sixth round): 150k on a slow drive, first run and a card

| # | User impact | Fix | Status |
|---|---|---|---|
| BC1 | The status line can never say Up to date: debt() counts kinds the machine cannot make (embedding, faces with no GPU), 1.6 s every 30 s | debt gated on kind.here(), as the step already is | shipped |
| BC2 | counts.unidentified counts rows the worker will never take (no tail, a derived copy): Catching up forever | one predicate, the worker's | shipped |
| BC3 | size() and days() pin the date index, defeating the folder range and the stars index: 44 ms vs 0.03 ms | the hint only for the bare library | shipped: 44 ms → 0.03 ms on a folder |
| BC4 | The tile ceiling is never enforced during a backfill: eviction runs only when idle | the sweep on a clock; free space re-read | shipped: every two minutes |
| BC5 | With the archive away the worker re-probes the same 64 away heads every step | the follower's attached list reaches every step; a kind that reads the original is not asked for a photograph whose every copy row is on an away drive (one with no copy row is still tried); +0.2 ms on the anti-join with the disk here | shipped |
| BC6 | Every owed head re-reads a drive marker file from disk | `attached_now` is drive id → root, looked at once a pass; `locate(roots=)` joins the tail to it and reads no marker; the test forbids a marker read during a step | shipped |
| BC7 | Adding a big folder saturates the library lane with a full-shelf poll every 500 ms | the scan's poll re-reads only what grows (the pages and the count); the shelves, drives, albums, people and labels are read once when the walk lands | shipped |
| BC8 | swept costs 1.1 s of the interactive lane: the folder tree is built there | the tree is a made answer like the facets (`_reshape` makes both on the sweep lane after a sweep that changed something; keyed by the stamp and the sweep count); the interactive lane makes it only the first time and answers with the last while a fresh one is made; the drives' markers are not re-read for it | shipped |
| BC9 | A search on screen re-runs the whole fusion per page every 2 s | the last search's ranked ids are kept with what they were asked of (words, seeds, view, space count, whether the words had a vector, the shape stamp, the sweep count, the denied set); a page and the two-second re-read are slices of it | shipped |
| BC10 | Each Rank draw pays two COUNT(DISTINCT) scans and a second parse of the round log: ~300 ms | seen()/rounds() memoised on the log's head; progress per sitting | shipped: 0.2 ms after the first draw |
| BC11 | rank.space() peaks at twice the matrix (~1.4 GB) while loading | counted first, one matrix allocated, filled row by row from the cursor; measured on 20k synthetic vectors: peak 194 MB → 93 MB, same time | shipped |
| BC12 | owed()'s on_screen ORDER BY is dead in the product and costs 886 ms when used | deleted; the docstring says closeness is the scoped first pass | shipped |
| BC13 | Ctrl+A at 150k ships 148,000 ids across the bridge and back on every verb | measured: the ids are 1 MB and 25 ms round trip; the verb itself was one decision row and one update per photograph in a Python loop. Now one window read of every subject's last word, one insert under the write lock, one sliced projection: at 148k on this machine reject 29.5 → 11.9 s, undo 32 → 23 s, restore 28 s, a page of 500 in 83 ms. Still tens of seconds for a whole library — a verb by scope with a count back remains the answer, its own round. Second round (09-12 night): the projection writes one statement per five hundred rows that want the same value instead of one per row (9,319 rows: 2.83 s to 0.30 s for the update alone); on the loaded machine a whole-library reject went 66 to 24 s, a folder of 12,747 picked and undone 7.0 to 3.3 s, a page of 500 picked and undone 513 to 186 ms; what remains is the decision insert's index maintenance (executemany and insert-select tie) and the verb's own Python over 148k rows. The bench times a page and a folder now | shipped |
| BC14 | The follower notices no card and no drive for the whole first archive sweep | the sweep not awaited inside the loop | shipped |
| BC15 | Pending cells repaint continuously: background animated, a paint per cell per frame | opacity on the veil | shipped |
| BC16 | The bench has no row for the verbs found slow; PERF_BUDGETS.md says no V2 bench exists | rows added, budgets per shape, the doc names the bench | shipped: plus a filename index (page at depth 96 → within budget) |
| FR1 | An import reads every file five times | `photos.copy_verified`: the bytes are hashed on their way through the copy and the copy is hashed once to verify; the row records that digest; the backup copies the same way (the third variant of copy-then-compare is gone); the card is read twice (identity, then the copy) and the copy once | shipped |
| FR2 | Staging opens every file's EXIF on the card: half a minute of seeks | deferred: the capture second is how a re-inserted card is recognised (the import renames files, so the name never matches); mtime would lose that; the honest answer is reading the tags on the intake lane with the count growing, which the stage already shows | shipped 09-12: the lane and the count were already so (`_stage` reads on the intake executor and says how many it has seen); what remained was every JPEG and HEIC opened twice, once for its shape and once for its tags. One open now serves both, pinned by a refuter that counts. A 4,000-file card on local disk: 0.99 → 0.74 s; on a card, half the seeks per display file. A raw is still two reads (libraw for the shown shape, the header walk for the tags) |
| FR3 | Every staged thumbnail decodes a raw on the bridge thread, unbounded | deferred to an import round with FR7: the stage's own lane and a key→cell map are one change | shipped, as the row of the same number under Import workspace |
| FR4 | The first grid tile always decodes at loupe size, so small embedded previews never qualify: 3 s a frame | decode at grid size when only the grid tile is owed | shipped |
| FR5 | Ejecting the card mid-import yields 1,900 unreadable and phase done | the run stops and says the card was removed | shipped |
| FR6 | A mixed import prints the same sentence twice (already vs skipped) | one clause for identity, one for the file already at its place | shipped |
| FR7 | The stage is not virtualised; a day checkbox costs ~4,000 DOM queries | a key→cell map; only what changed is touched | shipped, as the row of the same number under Import workspace |
| FR8 | The first-run path is clean | measured: keep | measured: keep |

## People (2026-09-10)

| # | User impact | Fix | Status |
|---|---|---|---|
| PE-M1 | Two groups of one person could only be merged by naming each the same word, by hand | the wall asks: pairs of groups close in the face space (NEAR ≤ cos < SAME, never a pair kept apart) as one inline card — two faces, Same person? Yes / No, Y/N keys; Yes heals as naming does (a name asked for when neither has one), No is a decision that keeps them apart | shipped |
| X47 | The People wall crashed on a face with no sample: icon() rejected a two-word class | icon() takes a class list | shipped |

## Refutation (people, sharpness, the command line; 2026-09-10, by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X48 | After the first No, every people rewrite raised (the apart query left out the decision's value) and the shelf, wall and per-photo people rows stopped updating for the life of the catalog, silently | the value is selected; a test says No and rewrites | shipped |
| X49 | A No was keyed on two exemplars, which move when a stronger face joins a group, so the question came back | the No is said about two faces and holds for whichever groups those faces are in now | shipped |
| X50 | A No never moved the rank lane's change key, so the wall's list kept the pair until the next launch | the key reads the apart family too | shipped |
| X51 | The same sharpness recipe answered from the loupe when it existed and the grid tile otherwise: numbers not comparable across the library, and the eye gates meaningless at 1,024 px | one rendition only, the loupe; `wants` is the loupe's own scope (`tiles.made`); a missing file is owed again, not failed; key zm4 | shipped |
| X52 | A face box off the frame's edge was measured on black padding, with a confident verdict; a face at the left or top edge lost its subject ratio to a negative slice | boxes and eye crops clamped to the frame; the ratio's block indices floored at zero; a test at the edge | shipped |
| X53 | A missing rendition file made a permanent failed row | `source` returns None when the loupe file is gone | shipped |
| X54 | The pass ran with no landmark model and every eye answer was None; old-key rows were read back | `here=faces.ready`; `of()` reads the current recipe only | shipped |
| X55 | `_grey` copied the loaded 4,096 px image and called `draft` on the copy, a no-op | the frame is decoded at a quarter scale from its own handle | shipped |
| X56 | The module claimed the frame map measured sharpness; it measures texture and noise alike | said so; only ever read as a ratio | shipped |
| X57 | Every details read shipped every face box and both eyes' numbers to the inspector, which says two words | the inspector gets subject and eyes; the record stays for the fit | shipped |
| X58 | The inspector's facts vanish with the archive away, since details() needed the original | with the original away, the cached metadata entry answers and the derived facts ride along; a test hides the file and asks again | shipped |
| X59 | The way back from a Yes unnamed one side: with neither side introduced the merge stayed half-applied; with both introduced a name was destroyed | the log replays: every face named after the Yes answers to what it answered to before (`unname_since`); a test merges Ada and Bob and gets both back | shipped |
| X60 | The card's Y/N never fired from the wall (the card could not take focus); N there opens the name dialog | Y anywhere on the wall answers the first question; the card is focusable and N is its own key; the arrows stay with the browser inside it | shipped |
| X61 | Answering dropped the keyboard on the floor: the focused button was replaced | focus lands on the next question, else the wall's cursor | shipped |
| X62 | Yes on two introduced people never said which name survives | the button says "Yes — both are Ada" | shipped |
| X63 | Pairs at or above SAME were never asked about, though greedy clustering leaves such groups apart | the upper bound dropped | shipped |
| X64 | The pair search walked every cluster pair in Python: 9 s at 3,000 groups | one matmul over the centres | shipped |
| X65 | `maybeSame` made 24 catalog reads for three cards | three pairs, six faces, one read | shipped |
| X66 | `>zzz` showed an orphan Commands header and Enter did nothing | "No verb on the screen matches"; Enter closes | shipped |
| X67 | A command emptied the box without telling it, so the grid kept the old search and Clear hid | the box is emptied the way Clear is | shipped |
| X68 | "Every verb on the screen" was 17 of 47 | every data-action has a tip, so every one is a command and has a tooltip | shipped |
| X69 | A leading space defeated the command line | leading space ignored | shipped |
| X70 | A command clicked a button captured earlier, detached by a re-render: silent no-op | the button is found again when chosen; if it left the screen, it says so | shipped |
| X71 | The sharpness pass said "Working" on the status line | Measuring focus | shipped |
| X72 | The harness stub carried no sharp facts and only a one-sided question | sharp on every stub photo; a second, both-unsettled pair; unname_since | shipped |

## Refutation of the refutation and the performance rows (2026-09-10, by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X73 | The wall's faces were zipped against a filtered list: with a tile missing (the archive away) every question showed the wrong person | each look carries its photograph and box; the wall keys on both | shipped |
| X74 | Two groups whose top sample is the same frame (two people in one photograph) showed the same face twice | keyed on the box, not the photograph alone | shipped |
| X75 | The word a face answered to before a Yes was read by id alone, not by authority: an imported name could outrank your own on the way back | the prior word is read as `_names` reads it | shipped |
| X76 | The way back from a Yes took back every word said after it, a later naming of someone else included | the Yes says the log ids it wrote; the way back takes exactly those; a test names a third person after the Yes | shipped |
| X77 | Y from a focused card never reached the wall: the button guard ate every letter | Y on the People view goes to the wall before the guard | shipped |
| X78 | With no sweep lane (tests, scripts) every cull remade the facets and the tree inline | ownerless stays lazy: made on the next ask | shipped |
| X79 | The shape stamp counted five things to use two, on every search and folders ask | two counts | shipped |
| X80 | The search memo missed a pick, a star, a round: the stamp counts rows | the log's last id is in the key; the seeds are sorted | shipped |
| X81 | A dead filter over the exemplars (its membership test was the summary's own) | gone | shipped |
| X82 | Choosing "Clear the search" from the command line reported failure: Clear hides itself once the box is empty | the button is found before the box empties | shipped |
| X83 | An edited photograph's grid tile stand-in stopped filling the stage while the preview was still being made | preview only once the loaded source is the rendition | shipped |
| X84 | Sharpness rows of an old measure sat in the catalog for good: never evicted, never read | `sharpness.tidy` at repair | shipped |
| X85 | A box wholly off the frame recorded a negative size | clamped both ends; a rendition's own longest edge recorded with the frame facts (a small original is measured at its own pixels) | shipped |
| X86 | A copy touched by an indexer between the write and the verify read raised instead of failing the verify | an OSError on the verify is "not verified" | shipped |
| X87 | The status line said Catching up with the archive away and nothing doing: the count did not know what the step knows | the debt is counted with the attached list too | shipped |
| X88 | Rows leaving between the count and the walk left a view over the whole buffer | copied when short | shipped |
| X89 | The Yes button named the survivor only when both sides were introduced | whenever one is | shipped |
| X90 | The wall was capped at three questions on the product side, so answering three left it empty until the next rewrite | every pair comes back; the wall shows three | shipped |
| X91 | `_refacet_pending` could stick if the lane refused the job | reset on refusal | shipped |
| X92 | Zoomed on a preview the chip claimed a percentage of the original | Fit · preview | shipped |
| X93 | Loupe eviction bounds sharpness coverage (the pass wants the plain loupe row, which the ceiling may drop first) | known: the ceiling is half the free disk; a photograph's sharpness is owed again when its loupe is remade | open |
| X94 | A ready loupe row whose file is gone counts as a locate miss for its root | known, rare; a sentinel would be the fix | shipped 09-12: no sentinel needed -- only a look at the disk counts as a miss; a kind that reads another answer (`kind.source`) saying None is owed, not away, so nine evicted loupes no longer skip a root's tiles for the step. Refuted by `test_an_answer_whose_file_is_gone_is_owed_not_away` |

## The owner's sitting (2026-09-11)

| # | Finding | Fix | Status |
|---|---|---|---|
| PW1 | Merging or renaming on the People wall felt slow: the rank lane reranked the whole space before it rewrote the groups, and the wall showed the old groups until then | people rewrite first on the lane (0.6 s on his catalog) and the pulse says so at once; the rerank follows only when rounds or the space moved | shipped |
| PW2 | The wall waited for the lane to wear a name | the card wears the name, or the two cards become one, the moment the verb returns; the true groups land underneath | shipped |
| PW3 | Typing a name someone already answers to gave no sign that Save joins them | the field says "Joins “Ada” — the two become one person" as you type | shipped |
| PW4 | The rail marked months only | every day with photographs leaves a hairline, longer the fuller the day; the word under the hand says the day and how many | shipped |
| PW5 | The fold tabs floated mid-height on the seams and a lid hung under the search box | the sidebar's and top bar's tabs share the workspace's top-left corner on the bar row; the inspector's sits on its seam at the same height; a hidden panel's tab waits where it was | shipped |
| PK1 | A console window titled with python.exe's path appeared while he worked | it was the headless UI harness's stub server run from this session, not the app; stopped | closed |
| PK2 | Task Manager and the taskbar said python | `scripts/make_launcher.py`: a copy of the base pythonw.exe beside the venv's pyvenv.cfg (the venv's own pythonw is a redirector that starts the base interpreter as a child, which is why it said python), wearing the compass and a version resource; runs the checkout in-process as "Azimuth Photo"; the Desktop and Start Menu shortcuts point at it | shipped |
| IM1 | The import dialog offers no Copy or Move | Copy / Move as the dialog's own choice, remembered; Move takes each file only after its copy is verified, and the line under it says so | shipped |
| IM2 | A photograph culled before comes back on every re-import | the decision log already holds the cull by identity (Empty Trash keeps the log), so nothing new is written down: a culled identity that no drive holds stays out, the import says how many, and one button brings them anyway; a test culls, empties, re-imports | shipped |
| PF1 | A double-clicked shortcut opened a second Azimuth on the same catalog | one at a time: a second launch brings the first window to the front and leaves | shipped |
| PF2 | A window that closed by itself left nothing to read | `logs/azimuth.log` under the home (rotating, 2 MB × 3) and every uncaught error on any thread written to it | shipped |
| PF3 | Nobody knew how long the app takes from click to first grid | every launch marks it in its own log: window shown +1.74 s, first page answered +2.47 s through the launcher on a loaded machine; budgeted in `docs/PERF_BUDGETS.md` (≤ 2 s / ≤ 3 s) | shipped |
| PF4 | An import's progress lives only in the panel and the status line | the taskbar button carries it (ITaskbarList3 through ctypes, no package added), set from the same status the panel polls; constructs on this machine, to be seen on the next real import | shipped |
| PF5 | The app never says which build it is | the keys sheet's foot says Azimuth Photo · commit · date, read from the checkout's own files; what to quote when something is wrong | shipped |

## Refutation of the sitting (2026-09-12, by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X95 | Bringing a culled photograph back left the log saying trashed: the next status rebuild would trash it again | bringing it back is a decision written to the log; the test checks it | shipped |
| X96 | The "bring them anyway" button lived in a panel that had already closed | the offer rides the toast, whose button says what it does; the toast takes a label | shipped |
| X97 | A remembered Move from a card would have deleted a folder's originals | the mode is remembered per kind of source; a folder starts on Copy | shipped |
| X98 | The wall's own word was overwritten by a stale read within milliseconds, then corrected seconds later | the wall keeps its word until the lane's count moves (`shaped` on the state) | shipped |
| X99 | An answered question came back on the wall until the lane rewrote | the same gate | shipped |
| X100 | The pulse told the grid before the rerank had written the order; a people failure stopped the rerank | the lane says so after the rerank as well; the people rewrite cannot stop it | shipped |
| X101 | With the top bar hidden its tab was half off-screen | the tab hangs from the seam; hidden, it sits at the top edge whole | shipped |
| X102 | A rename to a name someone already wears showed two cards with that name | the card folds into the twin at once, as the rewrite will | shipped |
| X103 | The join note promised a merge on a case difference the model would not make | the join keeps the spelling in use: "alice" joins "Alice" | shipped |
| X104 | Enter on an empty import was a silent no-op | "Nothing is checked." | shipped |
| X105 | The day hairline and the month tick sat on different baselines and insets | one baseline, one inset | shipped |
| X106 | The join note was announced as an alert on every keystroke; the Copy/Move choice had no pressed state | a status when it is a note; aria-pressed on the choice | shipped |
| X107 | A second launch could bring an Explorer window named Azimuth Photo to the front | the window must be titled Azimuth Photo and owned by an Azimuth process | shipped |
| X108 | A second launch during the first's startup exited with nothing on screen | it looks for the window for five seconds first | shipped |
| X109 | The log took INFO from every library in the process | the app's own modules at INFO; libraries keep their level | shipped |
| X110 | A worker thread ending by SystemExit was logged as an uncaught error | not any more | shipped |
| X111 | The launcher hard-coded python312.dll and skipped a stale copy | the running interpreter's version, always the base's own | shipped |
| X112 | The launcher's version named a commit the exe does not run | the day it was made; the log names the commit | shipped |
| X113 | A Start Menu shortcut in a subfolder would have been left stale beside a new one at the root | every shortcut of that name is rewritten; one is made only when none exists | shipped |
| X114 | Writing resources invalidates the interpreter's signature | known and said in the docstring: a local file carries no mark of the web; a scanner that objects gets an exclusion | closed |

## Taste and sharpness program (2026-09-10, from docs/taste-and-culling-research.md)

| # | User impact | Fix | Status |
|---|---|---|---|
| E2-1 | Nothing said where the sharpness sits: a missed focus and a bokeh portrait looked alike to every surface | a `sharpness` cache kind on the CPU behind the tiles: the frame's local-variation map (p50/p75/p90), the largest face's box against the frame (subject ratio), and Zhu–Milanfar's noise-aware measure on every face crop from the 4,096 px rendition (gated by crop size); the inspector says the numbers, never a verdict | shipped |
| E2-2 | Eyes: open/closed/can't tell, and each eye's own sharpness | the 106-landmark model buffalo_l already ships runs in the sharpness pass on each face over 48 px; each eye's contour height over width is its openness and Zhu–Milanfar on the padded eye crop its sharpness; the photograph's word is the largest face's, said only when both eyes are readable and agree (open ≥ 0.25, closed ≤ 0.15, else Cannot tell); the inspector says it; OCEC left out (the contour alone read every proof face; add it only if E5 shows blinks slipping through) | shipped |
| E1 | The head was two fits in a row: strengths from the rounds, then a ridge direction fitted to those strengths, blended back by a trust weight | one Plackett–Luce fit over every round learns the direction and each photograph's residual together (`rank.fit`); measured five-fold on 4,308 of the owner's rounds: the picked photograph placed first in 45.3% of held-out rounds before, 51.6% now; pairwise 80.4% → 82.2%; 3.4 s a fit on the rank lane; `taste.py` deleted | shipped |
| E3 | A per-genre residual, so a night lake and a portrait stop sharing one scale | rejected by measurement: 16/32/64 k-means cluster offsets beside the direction move round accuracy 0.516 → 0.515–0.520 (noise); each photograph's own residual in the one-stage fit already absorbs its genre | rejected |
| E4 | Learn's uncertainty was a count of rounds (1/√(1+n)), not what the rounds could still teach | the fit hands back its own uncertainty (`rank.fitted`: the Fisher information plus the pull) and the rank lane keeps it for Learn's draws, so a draw costs nothing more. Measured with the shipped mode, five seeds, 600 rounds of 9 over 2,000: top-decile recall .473 → .502 on every seed; the whole order unchanged (.342 → .340). The finding round still keys on wear, where the fit's uncertainty ties | shipped |
| E5 | Burst-best within cadence stacks: facets alone against Sean's picks | the copy has no cadence stacks, so the next honest question was asked: within one shooting day, do the facets tell a pick from the rest? Measured on the six days holding picks (4,076 photographs, 158 picks, every loupe on disk): face sharpness AUC .48, subject ratio .49, eyes open .50 (every frame's eyes were open), frame texture .52, eye sharpness .50 — no signal. The facets stay facts in the inspector; no cull mark is earned yet | measured |

## Panoramas (2026-09-12, from docs/panorama-research.md)

| # | Finding | Fix | Status |
|---|---|---|---|
| PN1 | A swept sequence is not recognised: every run of frames is a burst to the library | `panorama.py`: the run around a frame (within 5 s, one camera, size, folder) judged on the 1,024 tiles by the overlap rule; the verdict kept once per run as a `sweep` cache row; S on a frame that keeps no beat stacks its sweep; the inspector says "4 frames sweep left to right, 49% overlap — S stacks them". A synthetic sweep is accepted and a burst refused in the suite; on his catalog 1 of 445 runs | shipped |
| PN4 | A sweep the library knows cannot be seen whole | the preview: the Panorama fact is a control that merges the sweep from its loupe tiles on the sweep lane (OpenCV's stitcher, the black canvas trimmed on an eighth-scale mask; his Mt Washington sweep in 3.4 s, 7,337 × 2,163 from 4,096 px tiles), kept as one file on the run and shown as a strip under the facts and, pressed, in the loupe as a preview; a stitcher that cannot place the frames is remembered as failed and the fact stays a fact. Evicted by the ceiling like any loupe (X166) | shipped |
| PN2 | No merge | full-resolution merge through the detailed stitching API reusing the detection's bundle adjustment: projection by field of view, blocks-gain before the seams, graph-cut seams, multi-band blend, auto-crop with the canvas kept; a 16-bit TIFF that becomes the stack's cover | open |
| PN3 | A merge that is not a photograph | the raw path: linear demosaic, stitched in linear light, a linear DNG with the first frame's metadata and a sidecar naming the members; boundary-warp edge recovery as one slider | open |

## Details (the thousand small things, 2026-09-12)

| # | Finding | Fix | Status |
|---|---|---|---|
| D1 | Thirteen lines could read "1 photographs" (the drag badge, the sidebar count, the import source and summary, a person's count, the search-kept notice, Empty Trash's notice, the selection heading); twenty more spelled their own plural | one word in the kit, `count(n, one, many)`, used by every surface that says how many | shipped |

## Sidebar, search, bars and dialogs (ninth round, 2026-09-12 morning, audited by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| AL1 | Since the keyboard round of 09-10, a focused photograph, card or face (all buttons) counted as a chrome control, so after a click or an arrow every verb key and the arrows did nothing; the journeys never saw it because they pressed from the body | a control is a chrome button outside the stages; the two inner guards it made dead are gone; the journeys press from where the keyboard is. Proven in the real app: click, X rejects | shipped |

## Loupe, Rank and People (eighth round, 2026-09-12 night, audited by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| LR1 | The live region and the status line lived inside the sidebar, so folding it (Tab, or F's clean room) silenced every spoken act and hid all work in flight, Stop included | both are the shell's: the status line at the sidebar's foot, a pill in the corner while there is work when the sidebar is folded, the live region a child of the shell | shipped |
| LR2 | A Rank survey (N on marked frames) outlived Rank when a sidebar door left it, and silently narrowed the whole library to those frames | the render clears a survey whenever the view is neither Rank nor a look taken from it | shipped |
| LR3 | The loupe's "cannot be shown" note was erased by the next render (the details read 120 ms later), leaving a black stage with no words | the failed source is in the store (`troubled`) and the note derives from it until the source moves | shipped |
| LR4 | An arrow in a look taken from Rank walked into the whole library from "nowhere" and showed photograph #1 | a look from Rank is one card: the arrows stay | shipped |
| LR5 | No on the People wall was the one act there with no way back | the No has Undo: the same word taken back, and the wall may ask again (each pair's last word counts) | shipped |
| LR6 | At 1366 px the Rank bar's progress sentence wrapped, the bar grew and every stage's bottom fell off the window | the sentence keeps one line with an ellipsis, as the search column does | shipped |
| LR7 | The keys sheet had no People section | the wall's keys, in its words | shipped |
| LR8 | The loupe's first-open lesson was armed only by double-click and E; Enter, Space, F, C and D never taught | the lesson is armed where the loupe opens | shipped |
| LR9 | Rank's size and mode buttons had no verb for the command line and no tooltip | each is an action with a standing tooltip; the disabled reason replaces it only while it holds | shipped |
| LR10 | Rank's progress and its two settings said nothing to a screen reader | the progress is a status; the settings say their word | shipped |
| LR11 | A faces read that failed was reported as "no face on this photograph" | the failure is said in the app's words | shipped |
| LR12 | A pick that did not record and a failed cull spoke the bridge's words | `why()` | shipped |
| LR13 | The loupe was named four ways (loupe, preview, Close preview) | the loupe is the loupe; Preview means a rendition with no larger truth behind it | shipped |
| LR14 | Two lines still said photo | photograph | shipped |
| LR15 | Three counts named no unit or grouped no digits | `numbered` | shipped |
| LR16 | Space picks in Rank and the sheet said only Enter | Enter / Space | shipped |
| LR17 | LR2's clear ran on the render N fires before Rank opens, so a survey never reached Rank (caught by this session's proof) | the survey is cleared only when the view leaves Rank or a look from it (`viewShown`); proven: N on three marked frames ranks "3 marked photographs", the All photographs door ends it | shipped |
| LR18 | LR3's failed source was never cleared, so a source that later loaded would still wear the note | cleared with the loupe | shipped |
| LR19 | Where the loupe returns to (`loupeReturnsTo`) outlived a loupe left by a chip or a name: later library loupes refused the arrows and Esc jumped into Rank | cleared by the render whenever the loupe is not up | shipped |
| LR20 | The status line at the sidebar's foot had no background, so a scrolled sidebar's rows passed behind its words; folded, its pill covered the filmstrip's first cells and took their clicks | the foot wears the chrome; the pill takes no pointer (Stop does) | shipped |
| LR21 | A source that failed once wore "cannot be shown" even after it loaded (a remade tile at the same path) | a load clears it (`onShown`) | shipped |
| LR22 | Undo of a No restored the decision but the wall did not ask again until a later rewrite (the cached summary had dropped the pair) | the wall re-asks at once; the lane's next rewrite keeps it | shipped |
| LR23 | `role=status` on Rank's progress spoke the whole sentence after every pick, on top of the pick's own sentence | dropped; the settings speak their word, the pick its name | shipped |
| LR24 | At 13 inches the sidebar was 190 px: "All photographs" (IA6's word) truncated in its own row and the wordmark wrapped; the proofs had only ever run at 1900 px | the sidebar's floor is the 210 px the narrow breakpoint already uses; `native_proof.py --size 1366x768` proves the 13-inch persona from now on | shipped |

## Import and inspector (seventh round, 2026-09-12 night, audited by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| IA1 | Under 1,050 px (the window's minimum is 900) the details column hides, and the import panel lives in it: a card came in with no source line, no Copy/Move and no Import button | the column stays while a card is in (`is-intaking` on the shell), at any width and through the right fold | shipped |
| IA2 | A notice with no way back left the standing Undo button beside words it did not describe: reject forty, an import finishes, "1,240 imported" with an Undo that restores the forty | the button is a promise about the words beside it: hidden for a notice; Ctrl+Z keeps the live way back for its eight seconds | shipped |
| IA3 | The loupe never said it was showing a merge (the chip's "Preview" is what an edit gets too), and the way back to the frame was undocumented | a caption beside the chip: "Merged from 4 frames · the Panorama fact shows the frame"; the fact toggles back; the picture's alt says what it is | shipped |
| IA4 | "Merging the sweep…" was a toast that cleared in 3.5 s while the merge took longer, and the fact stayed pressable | work in progress goes to the status line and clears when it lands; a second press while it runs is nothing | shipped |
| IA5 | The merge was reachable only by a tooltip on a line of text; the command line could not offer it | the sentence says the press ("— press to merge a preview" / "— press to see the merge"); the control is `merge-sweep` with its tip, so `>` finds it | shipped |
| IA6 | "Photo" and "photograph" named the same thing in adjacent surfaces (the eyebrow, the empty state, three landmark labels, All photos) | one word: photograph | shipped |
| IA7 | The Names fact copied "Ann · Bob" to the clipboard; a person could not be reached from it | one control per name; each narrows to every photograph of them (the Camera pattern) | shipped |
| IA8 | "Where" (the drive) and "Place" (the capture location) both read as location | the drive row is Stored | shipped |
| IA9 | The selection's Cull line counted the rejected and dropped them on the way to the screen | "12 rejected" joins the census | shipped |
| IA10 | A roll could read "1 frames" and "(top)", and an emptied name hid the name that would be used | `numbered`, "The top folder", the proposed name as the placeholder | shipped |
| IA11 | The command line offered "Cancel the import" for a button that says Back and cancels nothing | "Close the import panel" | shipped |
| IA12 | The keys sheet listed four of the stage's seven keys | Shift+Arrows and Home/End, in the grid's words | shipped |
| IA13 | The kind choice was a bare div of buttons: a screen reader heard no group and no chosen answer | a labelled group with `aria-pressed`, as Copy/Move already was | shipped |
| IA14 | A card with nothing to bring in opened a blank black stage | the grid's empty state with the import's words | shipped |
| IA15 | Checking photographs, the whole job of the stage, said nothing to the live region | each act says "12 photographs checked" | shipped |
| IA16 | A copy that failed said nothing | "That could not be copied." | shipped |
| IA17 | The import's own refusal printed the bridge's raw words beside the `why()` that exists to prevent it | `why(error, 'The import')` | shipped |
| IA18 | The merged strip was a picture with a click handler: no keyboard, no role, an `img` inside a `dl` | a button in a `dd`, with the focus ring | shipped |
| IA19 | The import stage was a fixed 190 px grid at 4K, and the Size slider was hidden while it was open | the stage lays out from the slider's own number (`--cell`); the slider stays | shipped |

## Refutation of X115-X128 and PN1 (2026-09-12, by a third model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X149 | The sweep judge read every frame's features and every pair before any gate, on the library lane the inspector waits on: a 128-frame burst was ten seconds, the catalog's longest run (325 frames) a minute | one pair at a time, each gate as soon as it can be asked; a burst is refused at its first pair (two frames' features and one match); a run past MOST (36 frames) is refused unread. Measured on the owner's copy: every run of three or more (446) judged in 37 s, 82 ms each; the 325-frame run 0.01 s; the one sweep still found | shipped |
| X150 | A run judged before its tiles were drawn was kept as "not a sweep" for good | nothing is kept until every tile is on disk; a run the library has not drawn is not a refused one | shipped |
| X151 | The verdict was keyed on the run's first frame alone, so a frame rejected or brought in left a stale run: S could pull a trashed frame back into a stack | the recipe carries a digest of the run's members; a changed run is a miss and a fresh judgement | shipped |
| X152 | `panorama._timed` took a date-only stamp (`… 00:00:00`) as a capture time, so 300 scans of one day were one run | `stacks._timed`, which refuses it, is the one clock | shipped |
| X153 | `projection.project` committed inside its slices, so a cull verb's own commit and rollback governed nothing: an undo over two families could land half | an act's projection (`only`) commits nothing; the caller's commit lands decisions and projection together, its rollback undoes both; a whole-table rebuild still lands in slices | shipped |
| X154 | Learn's uncertainty began as an empty dict, so until the lane's first fit (after the first sweep, minutes on a slow disk) every σ was "never judged" and Learn dealt the leaders | it begins as None; the first Learn draw makes the fit over the rounds alone once (310 ms on the owner's 4,308 rounds) and keeps it until the lane's own replaces it | shipped |
| X155 | The BURST gate (median overlap > 0.85) could never be the refusing gate under OVERLAP_MOST 0.65 | deleted; the first pair's overlap refuses a burst | shipped |
| X156 | `sim_learn`'s fisher lane was a second copy of the shipped mode with two different tie-breaks; the E4 numbers are the learn lane's, which calls the shipped mode | the fisher lane deleted; `choose_band`'s comment says what it sorts by | shipped |
| X157 | An idle lane stepped every five seconds, and an idle step on a caught-up catalog walks every living row per kind (100 ms at 8k on the owner's copy, seconds at 150k): a core spent on nothing, per lane, forever (found by this session's bench while the refuter ran) | an idle lane sleeps until nudged -- a sweep, a look, an import, a drive coming or going (the follower nudges now) -- with a sixty-second clock as the safety net | shipped |
| X158 | The grid spec still held the last-row rule of 08-19 (never taller than the size) after F1 (09-09) made a short last row fill the width; the node spec had failed since, and the full check with it, unnoticed because only the Python suite was run (found by running `azimuth-check` whole) | the spec holds the shipped rule: a short last row stretches to the width within 1.6x the size and never past it; the whole check passes end to end | shipped |
| X159 | The smoke printed each fault once per probe: six copies of one fault | the last answer holds every fault the page kept; it is read once | shipped |
| X160 | A failed build refused the launch silently under pythonw, after the catalog was already open, even when yesterday's good document was there (node_modules gone) | the build runs before anything is opened; a failed build is logged and the document there is opens; only no document at all refuses | shipped |
| X161 | `version()` let StopIteration out when a ref was neither loose nor packed | no build to name is an empty answer, and the foot stays empty | shipped |
| X162 | Two builds at once (the window and a proof) shared one temp bundle name and could refuse each other | the bundle is named per process | shipped |
| X163 | The sweep's tile guard named the plain tile; a run with an edited frame (tiles keyed by the edit) was never judged and never kept, walked on every inspector open | the frame's own rendition is looked at when the plain tile is not there | shipped |
| X164 | "Learn's uncertainty before a lane's fit" lived twice: in `rank.candidates` and in `Library.rank` | one home, `rank.uncertain(conn)`; the library keeps its answer, the bench and the scripts call it | shipped |
| X165 | Two owed-work sources do not nudge an idle lane (a model's weights arriving out of band; `set_date`'s forget, which re-reads at once anyway) | named; the sixty-second clock covers them | held |
| X166 | The ceiling's sweep evicted a merge row (every evictable kind is, not only the worker's) and then died on its name (`by_name[name]`), leaving every file after it in the batch on disk with no row: the ceiling held in the catalog and not on disk | a kind the worker was not handed is unlinked like any other | shipped |
| X167 | The merge mixed 4,096 and 1,024 px tiles when only the frame in the loupe had its loupe tile (the ordinary state): the stitcher scales by its first image and dropped frames or failed | one size for every frame: the loupes when every frame has one, else the grid tiles | shipped |
| X168 | A frame whose tile was missing was remembered as "the stitcher could not place the frames", for good | a tile not there is not a refusal: nothing is remembered | shipped |
| X169 | Develop on a merged frame painted the edit and every render snapped the loupe back to the merge; the face key mapped the frame's box onto the merge | the stand-in yields when the edit panel is open and when a face is asked for | shipped |
| X170 | The merge held every frame of a run at 4,096 px at once, thirty-six frames being over a gigabyte before the stitcher's own copies | runs past eight frames merge from the 1,024 px tiles | shipped |
| X171 | The merge's real error was overwritten by "The frames could not be merged" | the bridge's own reason stands when there is one | shipped |
| X172 | The grouped projection made the rerank's own projection two to three times slower: twenty-four thousand distinct (elo, stars) over a few rows each became a statement per group (25 s to 59 s over 150k) | the hybrid: groups of sixteen rows or more land in chunks of five hundred, the rest keep the one bound statement (executemany) in the rows' own read order (out of order the same rows were three times slower again). Measured on the 150k copy, a rerank shape of 44,455 distinct values: the old loop 9.6 s, the hybrid 10.3 s (the first run of either is cold, 21 s); a cull shape of 12,879 subjects 0.48 s | shipped |
| X173 | The face key on a merged frame swapped the picture and placed the zoom from the merge's dimensions (the old image's naturalWidth survives a src change until the new one loads), and skipped the first face | the frame's pixels are awaited (`decode()`) before the zoom is placed | shipped |
| X175 | The import stage's new empty state was a box over the whole window (the stage had no position of its own): Back, the kind buttons and the sidebar could not be pressed | the stage is the box | shipped |
| X176 | The merge wrote the import's status field: a Stop button and "Importing…" appeared for a merge, and a merge wiped a running import's line | the merge is `doing`, the field Export uses | shipped |
| X177 | The Size slider, kept during import, laid out the hidden grid at zero width and scrolled the stage to a phantom position | the slider writes the number and stops there in the import view | shipped |
| X178 | The loupe's caption said the fact shows the frame while the fact said "press to see the merge": the merge was module state no render read | the merge lives in the store; the fact's sentence and tooltip follow it ("press to see the frame", "Show the frame again") | shipped |
| X179 | The command line named the merge control by a fixed word for three meanings | a control's own tooltip wins over the table's word | shipped |
| X180 | The right fold tab folded nothing while a card was in and flipped its arrow | the tab is not shown while a card is in | shipped |
| X181 | The command line promised "Close the import panel" for a Cancel that discards the staging | the button's own words (Cancel / Back) are what the command line offers (X179) | shipped |
| X182 | Size at its top end made a stage cell wider than the pane at the minimum window | a cell is never wider than the pane | shipped |
| X183 | The loupe caption shared its band with the first-open hint | the caption keeps to its corner with a width; the hint yields while a merge is shown | shipped |
| X184 | The grouped projection listed a key once per row that carried it, so two rows of one identity were written by two statements and counted twice (the suite's reindex test caught it; committed past a red suite, which is its own lesson) | each key once, in the rows' order | shipped |
| X185 | The merge survived every way out of the loupe but Esc (a chip, a name, a folder, an album), so Enter on the frame later showed the merge unasked | the render clears it whenever the view is not the loupe | shipped |
| X186 | The command line, reading tooltips now, printed every key twice ("… (Tab)" and the Tab cap) | the tooltip's key suffix is dropped for the label | shipped |
| X187 | The strip said "Open the merged preview" and, with the merge shown, closed it (the fact's toggle) | the strip only opens, and says "The merge, shown in the loupe" while it is | shipped |
| X188 | `doing` had three owners: a merge finishing blanked a running export's line (and the reverse) | a job clears only the word it said (`busy`) | shipped |
| X189 | The first-open hint, hidden for a merge, never came back that session | it yields to the merge and returns with the frame while the loupe is still teaching | shipped |
| X190 | The command line's key strip took a key written by hand into a title (Done (D), Back to the library (Esc), Import from the card (I)) and had no cap to put back | only the key the table derives is dropped; a hand-written one stays | shipped |
| X191 | `busy` compared words, and two merges say the same word: the first to finish blanked the second's line and freed the in-flight slot | a ticket per job, not its word; the in-flight guard is a set | shipped |
| X192 | pytest.ini said a timing test is never part of the default run, but nothing deselected it: the guided filter's 200 ms budget failed the whole check twice tonight on a loaded machine | the marker is mechanical (`addopts = -m "not bench"`); `-m bench` runs them when asked | shipped |
| X174 | `cache.evict` protects only the never-evict kinds it is handed; a caller with a partial tuple would have a never-evict kind's rows and files taken (the worker hands every kind, so unreachable today; the X166 test uses a partial tuple by fixture) | held: the worker is the one caller and is handed every kind | held |

## Refutation of D1-D5, PF4, PF5, E2-3 (2026-09-12, by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X129 | `sharpness.tidy` dropped every failed row at every start, so a photograph the measure cannot read was re-owed and re-failed forever (work.py: a failure is an answer) | the one-time drop rides the repair's residue marker (bumped to 2026-09-12); `tidy` drops old-recipe rows alone | shipped |
| X130 | The page's fault hook kept every fault and reported each over the bridge: a fault in a frame handler was sixty reports, sixty threads and sixty log lines a second | the hook lives in the document before the bundle (v2.html): each distinct fault once, fifty at most; the app hands it the reporter when the bridge is up and reports what was kept before | shipped |
| X131 | The taskbar object was made on one bridge thread (COM, apartment) and used from every later one; the failure was silent | the object is made, used and released within each ask; `Desktop.close` clears the bar | shipped |
| X132 | `version()` claimed a frozen bundle's stamp; there is none | the docstring says the checkout, or nothing | shipped |
| X133 | `version()` read the loose ref; after a `git gc` the ref lives in packed-refs alone and the foot went blank | packed-refs is read when the loose ref is gone | shipped |
| X134 | The smoke parsed "PROBE failed: …" as JSON and died with a traceback instead of its own verdict | only answers (`PROBE "`) are read | shipped |
| X135 | The D3 rename turned the class `stage-day-count` into `stage-day-tally`; the stage's day counts lost their dimming | the class name restored; a class is markup | shipped |
| X136 | `is-bare` hid the bar's controls on every launch until the counts answered, then they blinked in | the bar is bare only once the library has answered (`!state.loading`) | shipped |
| X137 | azimuth-check's comment said the specs check the bundle on a headless runner; nothing loads it there | the comment says what happens; a headless runner builds the document and opens nothing | shipped |
| X138 | Three lines still read "1 photographs" (freeze, save a search, the track), one "One day" became "1 day" | `numbered` on the three; "One day" kept | shipped |
| X139 | The smoke slept a fixed eight seconds; a cold WebView2 or a loaded machine refused a good build | the probe is asked six times, two seconds apart after four; the first whole answer wins, any fault refuses | shipped |
| X140 | The smoke left a home in `%TEMP%` per run | a temporary directory, removed after | shipped |
| X141 | The smoke stepped the tile kinds alone, not the metadata kind the app steps first | the smoke steps metadata then tiles, the app's own order | shipped |
| X142 | A comment read "re-lastLayout" after the rename | "re-layout" | shipped |
| X143 | A foot with no build to name showed "Azimuth Photo" and never asked again | the foot is written only with a commit | shipped |
| X144 | The sharpness pass holds the RGB frame and its contiguous BGR copy at once (two full frames) | held: OpenCV needs the copy; the source is dropped as the function returns | held |
| X145 | The fault hook was installed after every module had loaded, so a fault while a module loads was never kept | the hook is in the document before the bundle (X130) | shipped |
| X146 | development.md named `web/.venv/Scripts/`, a path git does not track; the paths gate failed on it and `azimuth-check` never reached the smoke | the guide says where without the path; the gates pass | shipped |
| X147 | A page that stopped polling mid-import left the taskbar's bar frozen for the life of the process | `Desktop.close` clears it; the bar is set fresh on every poll (X131) | shipped |
| X148 | The window opened whatever document was built last (D6, found by this session while the refuter ran) | see D6 | shipped |

## Refutation of E4, BC13, FR3, FR7 (2026-09-12, by a second model)

| # | Finding | Fix | Status |
|---|---|---|---|
| X115 | Learn's draw recomputed the uncertainty from the log on every click: +700 ms at 150k | the fit hands its uncertainty back with its scores; the rank lane keeps it; the draw reads it | shipped |
| X116 | The E4 numbers came from a proxy strategy, not the shipped mode, and the simulator's draws were unseeded | measured with the shipped mode over five seeds; the simulator seeds the global generator; the row says what reproduced | shipped |
| X117 | The uncertainty read the sort index's rounded scores, not the fit's | it is the fit's own, computed at the fitted scores | shipped |
| X118 | The finding round sorted the band by the fit's uncertainty, which ties at the top | the band keys on wear again; the teaching window on the fit's uncertainty | shipped |
| X119 | The batched cull read the last id outside the write lock: a concurrent decision aborted the whole verb | `decisions.decide_many`: the lock first (BEGIN IMMEDIATE), the same guards as `decide` | shipped |
| X120 | A second, slower copy of the latest-per-subject query (7.4 s vs 3.2 s at 148k) | one window query narrowed to the selection, at rank 1 for the last word and rank 2 for the word before | shipped |
| X121 | The projection held the write lock for the whole verb | through `projection.project`, in slices, as every projection is | shipped |
| X122 | Restore still asked one query per subject | the word before the last, for everyone at once | shipped |
| X123 | Undo answered in family order, not the order asked | in the order asked | shipped |
| X124 | The inlined insert dropped `decide`'s guards (a blank subject went in) | `decide_many` keeps them | shipped |
| X125 | A running import starved the stage's thumbnails and each ask held a thread | thumbnails on their own pair of workers; a closed library answers nothing | shipped |
| X126 | The stage kept every detached cell after Cancel | the maps are cleared with the stage | shipped |
| X127 | The FR7 comment overclaimed: a day's box is one write per photograph, not a few dozen | said plainly | shipped |
| X128 | E1's headline disagreed with itself (51.6% vs 52.5%) | 51.6% everywhere | shipped |


## Polish (first cloud round, 2026-09-12)

The first round run by the `polish` workflow from a cloud session: eight scouts, one per surface, against `docs/ui-architecture.md`; eight findings taken of thirty-two, each refuted before it was fixed, each fix in its own worktree with the check green and a harness probe; the rest deferred to the next pass. Landed in one pull request.

| # | Finding | Fix | Status |
|---|---|---|---|
| PL1 | An arrow in the filmstrip moved is-current and aria-selected but left the keyboard on the old cell (two focus rings, a selection changing on an unfocused option) | renderStrip reads hadFocus once and, when the strip held the keyboard and the cursor moved, focuses the new current cell; every built option says aria-selected="false" | shipped |
| PL2 | Six shell strings dropped or misnamed the counted noun: the likeness title said "More like 3 photos", the search box "Search your photos", the folder menu "Forget missing photos…" (template and app.js), an empty album "Drag photos… any photo", the day chapter's tooltip "Select the 8 of this day" with no accessible name, and Trash's Restore printed an unformatted count. | All say photographs, through numbered() where a count is shown; the chapter's title and aria-label read "Select this day's N photographs"; Restore uses toLocaleString. Proven in the harness probe (search box, chapter, menu, two-seed likeness title). | shipped |
| PL3 | The pass key V, a folder, a chip or Esc un-narrowing reload the grid and leave the keyboard on the body: the next arrow still works, but Tab folds the panels and a screen reader loses its place | the one handoff runs after the grid paints and fires on a finished load as well as a view change, giving the keyboard to the grid's own tab stop; say() only on a real view change | shipped |
| PL4 | The loupe's first-open lesson was counted on opens that hid it: F from the grid and a look from Rank open the clean room, where the hint was display:none, and burned all three lessons unseen | the hint keeps the clean room as the caption and zoom chip do (bottom 14px); a lesson counted is a lesson shown | shipped |
| PL5 | Rank's arrow keys moved the accent border but not the focus ring, and the cursor card said no aria word: after two ArrowRights two photographs wore a cursor, Enter picked the one without the ring, and a screen reader heard nothing on the move (`rank.js` `renderSelection`) | `renderSelection` sets `aria-current="true"` on the cursor card, strips it from the others, and when the keyboard is already on the stage focuses the cursor card with `preventScroll`; `render` builds plain cards and calls `renderSelection`, one writer for both paths. Harness before/after: `{selected:2, current:-1, focused:0}` -> `{selected:2, current:2, focused:2}` | shipped |
| PL6 | Freeze, Delete or a completed Rename from the keyboard dropped the focus to the body: the album shelf's rebuild did not carry the standing row, and the menu was hidden past hideMenu | render() refocuses the same album after the rebuild, the row that took its seat after Delete, else the Albums + button, as the folder tree does; the menu closes through hideMenu so the focus returns to the row that opened it | shipped |
| PL7 | Leaving Rank by Done, Esc or G dropped the keyboard on the body: the shell's handoff called grid.focus() on a .photo-grid with no tabindex, and onLeave()'s loadView() rebuilt the cells under any focus that had landed, so the next arrow, P or X went nowhere and a screen reader heard "Library" with no position | rank.close() focuses the grid's roving cursor cell (.photo-cell[tabindex="0"]) after onLeave(), falling back to the Rank button that opened the sitting; .photo-grid gets tabindex="-1" like the other stages so the shell's fallback can hold | shipped |
| PL8 | (refuted) | Refuted as framed: the proposed fix would silence something a screen-reader user relies on, and the "wrong elements" framing misreads what the count's live region does. | rejected |

## Rank, from the owner's sitting (2026-09-12, second)

| # | Finding | Fix | Status |
|---|---|---|---|
| RK1 | "the rank game still lags esp on 2 image mode": the buffer held one set's worth, a pair pick takes both cards, so the next pick waited on the bridge and, on a wide pair, on a 4,096 px decode | the buffer holds two sets' worth and the fill asks for the remainder; a card paints the decoded bitmap when it is ready, else the grid tile at once with the loupe swapped in when its decode lands; the swap is synchronous in the harness (msToPaint 0) | shipped |
| RK2 | "ditch the orange outline that shows on the next image on the previous pick": a keyboard pick kept the cursor at the picked slot, so the stranger arriving there wore the accent | no cursor survives a pick; the keyboard stays on the stage (the numbers, Enter and, in a pair, the arrows still pick), and in a set an arrow brings the cursor back | shipped |
| RK3 | The harness rebuilt its document only when it was missing, so a proof could run against the UI of an hour ago | the window's rule: rebuilt when any source under `web/static/v2/` or the template is newer | shipped |

## The large fixture, first build (2026-09-12)

| # | Finding | Fix | Status |
|---|---|---|---|
| LF1 | The pin assigned each frame's day, hour and minute from its own item id and only the seconds from its place on the page, so "consecutive frames consecutive seconds" never happened: 6,137 frames, three shared minutes, no stack proposals | the date is a function of the manifest: one photographer's catalogued month falls in fives three seconds apart, each five with its own day and minute; `--redate` reassigns without a download | shipped |
| LF2 | Every fixture frame says its camera is "Kodachrome", so the cameras facet has one entry over 6,137 photographs; a facet with one value proves nothing about the facet | leave until a proof needs cameras; the medium is the honest answer for a scan, and a second constant would be a lie of a different kind | deferred |
## Craft, from the owner's sitting (2026-09-12)

| # | Finding | Fix | Status |
|---|---|---|---|
| CR1 | "Landscape photos vs landscape orientation": the search drop offered "Landscape" under Kind and shape and the chip said "Landscape", which reads as a subject; the drop and the chip each held their own copy of the three words; adding a chip from the search box was answered by nothing, its removal by a toast | one vocabulary, exported from the filter bar and used by the drop: "Wide (landscape)", "Tall (portrait)", "Square"; a filter added from a text field is answered in the chip's own words ("Filter added — Wide (landscape)."), the mirror of the removal toast. The harness stub answered `facets` with an empty object, so the drop had offered no shape for any probe to see; it answers the verb's four lists now | shipped |
| CR2 | "the x on the filter toast isnt fully on the toast": the chip's × is a 16 px button holding a 16 px icon but inherited the button rule's 6 px side padding, so the icon spilled six pixels past its button and sat on the pill's curved end (measured: icon right edge 1 px from the pill's border) | the button has no padding and centres its icon; the pill's right padding is 8 px. Measured after: icon inside its button, 9 px from the pill's edge | shipped |

