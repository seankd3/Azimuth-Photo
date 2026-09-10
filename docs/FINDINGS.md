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
| FR3 | Two clicks and two surfaces to add one folder | picker first, then the dialog about that folder | shipped |
| FR4 | If proposing a home throws, first run is a black window | open the dialog first, fill the path after | shipped |
| FR5 | Esc on the home dialog is a silent no-op | one sentence saying why | shipped |
| FR6 | 'Point me at your photos.' is the product's only first-person sentence | 'Where are your photographs?' | shipped |
| FR7 | Two labels for one control: Add folder / Add a folder | one label with the ellipsis | shipped |
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
