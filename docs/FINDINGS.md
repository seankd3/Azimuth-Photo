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
| SC1 | Arrowing through the drop is hijacked by the resting mouse: scrolling a row under the pointer fires hover and the cursor jumps | hover moves the cursor only after real pointer movement | open |
| SC2 | The card that searches what you typed is the last of ~30 rows, below the fold | emit the everything row first when there is text | open |
| SC3 | The ⏎ badge sits on one row while Enter runs whatever the cursor is on | the ⏎ hint rides the cursor row | open |
| SC4 | The bar says 'Loading your library…' during a search | 'Searching…' when seeking | open |
| SC5 | Recents vanish the instant you type | recents pass through the same match filter, first | open |
| SC6 | A recent search cannot be forgotten | a × on each recent row | open |
| SC7 | The drop is invisible to assistive tech; no announced cursor | combobox/listbox roles and aria-activedescendant | open |
| SC8 | Folding the top bar with the drop open strands the drop | the panel toggle closes the drop | open |
| SC9 | More like this silently throws away chips, folders and album | keep the scope; drop only the query | open |
| SC10 | The like pill floats over the import workspace | hidden while importing, like its siblings | open |
| SC11 | Nothing shows the seeds of a likeness search; any click on the pill ends it | label opens the seeds, a real × clears | open |

## People wall and shelf

| # | User impact | Fix | Status |
|---|---|---|---|
| PE1 | The face wall has no keyboard | a cursor on the wall: arrows, Enter, N, Esc | open |
| PE2 | Naming is one verb in two grammars (right-click in the shelf, a button on the wall) | one verbsFor(person) for both | open |
| PE3 | Someone is detected by string prefix while settled is on the entry | pass settled from both callers | open |
| PE4 | Naming has no Undo | undo.show with the former name; an unname verb | open |
| PE5 | One person wears three shapes across drop, shelf and wall | one .face primitive sized by a variable | open |
| PE6 | The wall's empty state is a black rectangle | the grid's empty state, with people's words | open |
| PE7 | The shelf's order is whatever the cache wrote | named first, biggest first, alphabetical | open |
| PE8 | The door row is the only row without a count | append the count | open |
| PE9 | A failed people read is silent | say it, or let the empty state say it | open |

## Import workspace

| # | User impact | Fix | Status |
|---|---|---|---|
| IM1 | The card is erased by default: Clear the card is pre-ticked | off by default, remembered once chosen | open |
| IM2 | The sticky day header paints over the context bar | top: var(--ctx), lower z-index | open |
| IM3 | The stage promises the grid's selection grammar and ships only the mouse half | arrows, Shift+arrows, Ctrl+A, Home/End on the stage | open |
| IM4 | A suspect stays faded after you tick it in | checked suspects are opaque | open |
| IM5 | A stopped or failed import still yanks you to Recently added | re-scope only when something came in | open |
| IM6 | Stop says nothing | 'Stopping…', the button disabled until it lands | open |
| IM7 | A running import has no Stop outside its reopened panel | the status line carries Stop while importing | open |
| IM8 | The card chip has no count, tooltip, shortcut or announcement | title with the key, aria-live once | open |
| IM9 | The day list and the day headers are the same control drawn twice | delete the panel's day list; the stage's day rows are the picker | open |
| IM10 | 'already imported?' asks what the library knows | say what matched | open |
| IM11 | The chosen kind is not remembered | recall the last answer when the guess fails | open |
| IM12 | An unnamed roll can become a folder named ? | fall back to the group, then Roll n; refuse illegal names | open |

## Timeline rail and chapters

| # | User impact | Fix | Status |
|---|---|---|---|
| TL1 | The rail eats clicks on the rightmost column of photographs | reserve a 44px gutter while the rail is up | open |
| TL2 | After a resize the rail lies until the next store update | render on the grid's resize frame | open |
| TL3 | When the day counts do not add up the rail shows and says Undated everywhere | fold emptiness into whether the rail shows | open |
| TL4 | January never gets a tick | no continue after the year mark | open |
| TL5 | The rail is unreachable from the keyboard | a slider role with arrow and page keys | open |
| TL6 | Nothing says which way time runs | a title, and the far endpoint year drawn | open |
| TL7 | Sort changes reflow the grid by a scrollbar width | no scrollbar in every sort; gutter stable | open |
| TL8 | A hovered rail shows a date label forever | hide on leave and after idle | open |

## Status line and pulse

| # | User impact | Fix | Status |
|---|---|---|---|
| ST1 | A drive going away is never said | an away branch above the calm one | open |
| ST2 | The number does not match the word: all owed kinds beside one kind's name | the kind's own count | open |
| ST3 | 'Up to date' flickers while work is owed | say Up to date only when the debt is zero | open |
| ST4 | The first run has no status line | a home-less sentence | open |
| ST5 | A screen reader hears the pace every two seconds | no aria-live on the ambient line | open |
| ST6 | Three sentences mean reading your photos | one vocabulary table | open |
| ST7 | The pace restarts at a third of the truth after every idle beat | seed from the instant rate | open |

## First run and dialogs

| # | User impact | Fix | Status |
|---|---|---|---|
| FR1 | Esc can close the app behind a modal: Rank or the wall are tested before the drive dialog | any open modal is the first rung | open |
| FR2 | Cancelling the folder picker leaves the drive dialog looking broken | say it, or close | open |
| FR3 | Two clicks and two surfaces to add one folder | picker first, then the dialog about that folder | open |
| FR4 | If proposing a home throws, first run is a black window | open the dialog first, fill the path after | open |
| FR5 | Esc on the home dialog is a silent no-op | one sentence saying why | open |
| FR6 | 'Point me at your photos.' is the product's only first-person sentence | 'Where are your photographs?' | open |
| FR7 | Two labels for one control: Add folder / Add a folder | one label with the ellipsis | open |
| FR8 | Python exception text is the dialog's error copy | map the closed set of refusals | open |

## Keyboard map

| # | User impact | Fix | Status |
|---|---|---|---|
| KB1 | Tab folds panels instead of moving focus, so the sidebar and bar are unreachable | owner's call: Tab was chosen to fold, LRC's own key | open |
| KB2 | There is no keyboard map in the app | a sheet generated from one SHORTCUTS table, on ? | open |
| KB3 | / yanks you out of Rank and runs under the loupe | gate by view; from the loupe close first | open |
| KB4 | Most controls have no tooltip or shortcut hint | titles from the SHORTCUTS table at boot | open |
| KB5 | Ctrl+A works in two of six views | route by view: import checks all | open |
| KB6 | Pick hides itself on click so focus falls and a second click clears | one button whose label changes | open |
| KB7 | Space means three things, documented nowhere | listed in the sheet; hinted on the chip and stage | open |
| KB8 | Y and N make a new word out of any search phrase | only a known word, or an explicit Teach | open |

## Details inspector

| # | User impact | Fix | Status |
|---|---|---|---|
| IN1 | No ISO, aperture, shutter or focal length | read them in tags, carry through details, one Exposure row | open |
| IN2 | A slider edit is described as a crop with the wrong key | cropped and adjusted said apart, D opens Develop | open |
| IN3 | Missing facts vanish instead of saying so | Taken, Camera, Where always speak | open |
| IN4 | The inspector shows one photograph while forty are marked | a set branch from the loaded rows | open |
| IN5 | The path is printed twice; the heading is not the filename | heading is the leaf, Folder is the prefix | open |
| IN6 | The Folder fact is inert | Folder, Camera and names move the view | open |
| IN7 | The ranking is stated twice | stars fold into the score row | open |
| IN8 | Place is raw coordinates | degrees with hemispheres, four decimals | open |
| IN9 | Nothing can be copied | click a fact to copy it | open |

## Two things that are one thing (second round)

| # | User impact | Fix | Status |
|---|---|---|---|
| U1 | Three answers to a heading that rides the scroll (rail, import day rows, grid bands) | one sticky-under-the-bar rule | open |
| U2 | Two day tallies in the import panel | one daysOf(); delete the second list (IM9) | open |
| U3 | Three spellings of whether a photograph is here (cell, loupe note, inspector) | one presence(photo) in kit | open |
| U4 | Modals escaped two ways | one open-modal rung (FR1) | open |
| U5 | Two dayTitle functions and two chapter walks | kit/days.js: title() and chapters() | open |
