# Stories — the design (2026-09-13)

Every photographer has the same unfinished work: thousands of frames from
which a few dozen must be chosen, so that each one is different, distinct
and strong, and then put in an order that says something. A trip, a
wedding, a year, a body of work, a client's day, one person across a
decade. The choosing is the same act every time; the shapes differ. This
document designs the workspace for that act, for any photographer. The
owner's statements that started it are recorded verbatim in
`MASTER_PLAN.md` §1.20; his own case (a long trip that ended in an
engagement) appears below as one worked example among the shapes.

It follows `docs/ORGANIZING.md`'s law (predicted, then earned) and
`docs/product-vision.md`'s non-negotiables: one coherent product, decisions
durable, computations rebuildable, no weak intelligence shipped. A story is
an album that knows its shape; nothing here is a second application.

## The one rule

**A scene is worth its best frame, and a story is a sequence of scenes,
each shown once.**

A frame's strength adds up across scenes and not within one: the
second-best of twenty near-identical frames is worth almost nothing once
the best is kept, however high it rates. So a set is judged by the sum over
scenes of each scene's best, never by the sum of ratings, and scenes are
compared by their best, never by their mean. The sequence a photographer
worked hardest on cannot flood the set, and the one frame they had one
chance at cannot be outvoted by a burst.

Two kinds of round follow, both written to the decision log Rank already
keeps: within a scene (which frame leads) and across scenes (which leads
make the story, in what order). Only ever one frame per scene is on the
across-scenes stage.

## What the practice says

Sourced from how photographers and their editors actually work (the
sources are listed at the end; claims marked ~ could not be verified
against a primary source). Each rule is followed by what it decides here.

| The practice | Source | What it decides in this design |
|---|---|---|
| One frame per moment: fourteen frames of one moment become one in the book | photobook workshop teaching (13) | the one rule; a scene is shown once |
| The edit happens in rounds with time between them: 27,000 frames to 1,000 work prints to 83, over a year | Frank, *The Americans* (1); Goldin's *Ballad*, weeks on a loft floor (2) | a story is never finished in one sitting: the wall is the next round's pool, "cut again" runs the pass over what was kept, and every round is a state the story returns to |
| "Keep the pictures we couldn't live without, and then keep cutting from there" | Heiferman (3) | the budget is a target, not a ceiling; the count line reads against it and offers cuts in the rule's order |
| A second pair of eyes is structural: the maker is the worst judge of their own darlings | Goldin's editors (2); Steidl, Mack (5, 6) | the wall exports as one contact sheet, so a story can be put in front of anyone without the app; the app itself stays single-author (see the rejections) |
| Sequence is composition, not narration: built on contrasts of horizontal and vertical, wide and tight, indoor and outdoor, gaze toward and away | Heiferman (4); Shore (9); Hido's pairs of pairs (10) | the wall shows each frame's shape and tone as the catalog already knows them (orientation, colour or black-and-white); the order is the person's and the machine never proposes one beyond time |
| Repetition without variation flattens a sequence, however strong each frame | sequencing teaching (12) | the distinctness guard works across the whole kept set, not only within a scene |
| The whole set has to be seen at once: prints on a floor or a wall | Goldin's edit (2) | the wall is a surface, not a list: every kept frame visible together, dragged into order |
| Delete far more than feels comfortable; fewer in front of a viewer beats more | portfolio advice (16, 17); delivery norms (15) | default budgets come from practice, not from the size of the shoot: a portfolio of twenty, a book of sixty to eighty, a delivery of fifty to a hundred an hour of coverage |
| Speed at the first pass, patience at the sequence | Photo Mechanic's users (19); Hido's months with a dummy (10) | the pass is one key per act and never waits; the wall is slow on purpose and remembers where the person stopped |
| Machine picks are trusted only when visible, reversible and learned from the person's own choices; opaque, irreversible scoring was the trust failure | Aftershoot's own reversal (20); `docs/taste-and-culling-research.md` | every proposal wears the tilde until a key confirms it; rejects go to Trash, never deleted; the fit learns from swaps inside the story |
| The final sequencing decision is never delegated: floor edits, book dummies, two-year dialogues | Heiferman, Mack, Hido (2, 6, 10) | the machine proposes leads and keeps count; the order is the person's |

Deliveries and edits in numbers, for the default budgets:

| Context | Shot | Kept | Source |
|---|---|---|---|
| *The Americans* | ~27,000 | 83 | (1) |
| A National Geographic story | 30,000–40,000 | 10–20 | (14) |
| A full-day wedding | 1,500–3,000 | 400–800 | (15) |
| A portfolio review | a body of work | 20–30 shown | (16) ~ |

## What the archive already knows

The structure of a story is already in the catalog; today it is spread over
surfaces that do not talk to each other.

| Fact | Where it lives today | Used for |
|---|---|---|
| When | `date_taken`, sessions by capture gap (`web/library.py`) | chapters, the spine |
| Bursts | the cadence law, `web/stacks.py` (`around`, four on one beat) | the first cut at scenes |
| Likeness | the embedding space, `web/embed.py`, `web/rank.py`'s `_spread` | scenes wider than a beat; the distinctness guard |
| Strength | Plackett–Luce ratings from rounds, `web/rank.py` | the lead of a scene; the order of leads |
| Technical facts | eyes open, subject sharpness, exposure (`web/sharpness.py`, `web/photostats.py`) | the lead, as facets the fit weighs (`docs/taste-and-culling-research.md`) |
| Shape and tone | orientation, colour or black-and-white, already facets | what the wall shows beside each frame for the person's sequencing |
| Who | People (`web/people.py`, `web/faces.py`) | chapters and beats; a delivery's coverage |
| Where | `web/places.py`: EXIF position, or a GPX track filled in by time | chapters |
| Gathering | Albums with rules, pins and exclusions (`web/model/sets.py`) | the story is one of these |
| Undo | the decision log, append-only | every act in the workspace has a way back |

Nothing new is inferred. The workspace arranges what is known and asks the
person the questions only the person can answer.

## The nouns

| Noun | What it is | Machine-made? |
|---|---|---|
| **Story** | An album with a scope, an order and a budget rule, that has been through the pass. The scope is a chip sentence, the same one every surface speaks. Plain albums stay plain. | no — the person opens one |
| **Chapter** | A run of scenes that share a session and, when known, a place. | predicted (tilde) until accepted or redrawn |
| **Scene** | A run of frames on one beat, widened by likeness: the cadence law's set, merged with its neighbours when the space says they are the same subject. | predicted; split and merge are decisions |
| **Lead** | The scene's frame in the story. Proposed by the fit; one keystroke to accept or swap. | predicted until accepted |
| **Beat** | A scene the story turns on. Proposed from shooting density and who is in frame; confirmed by the person. | predicted until confirmed |
| **Budget** | The story's size and the rule that holds it: a number, a floor per chapter, or coverage. Chosen once when the story is opened. | no |

Vocabulary is fixed by the canon: photographs, Rank, Best, Picked, Albums.
Story, Chapter, Scene, Lead are the new words and they are ordinary ones.

## One pass, many shapes

Every kind of album a photographer makes is the same pass over a different
scope, with a different order and budget rule. None needs a surface the
story does not have; the test of the design is that none ever will.

| Shape | Scope (chips) | Chapters | Order | Budget rule | What matters most |
|---|---|---|---|---|---|
| **Story** (a trip, a project) | a span of time | sessions with places | time | a number; over it, cut the weakest lead of the most-represented chapter | the beat, the pulse of the spine |
| **Delivery** (a wedding, a job) | a day or a client's chips | the day's parts: sessions within it, who is in frame | time | coverage: every scene represented, N per scene growing with its length (eighty frames of the first dance deliver five, three of the rings deliver one); defaults from practice, fifty to a hundred an hour | the second look before export, so nothing is missed; then cull the rest in one act |
| **Review** (a year) | a year | months | time | a floor per month so a quiet month still shows, the rest by shooting density | the guard across the year: the same lake in March and October is one frame |
| **Portfolio** (a body of work) | everything, or `Label: Portraits` | none, or by subject | strength, then by hand | twenty to thirty; the guard is the instrument: every lead unlike every other | across-scene rounds in Rank's tournament mode, where the leaders meet |
| **Subject** (a person, a label) | one chip | time or none | time | a number or coverage | a smart album that has been through the pass; new matches arrive as unwalked scenes |

Two things fall out and cost nothing: a smart album is a story that has not
been walked yet, and stays alive as new photographs match its rule; and the
budget rule is the whole difference between shapes, one setting shown on
the count line ("312 of 4,000 · every scene · 6 alike").

**A worked example.** A long trip: the coasts, a city, three days in the
mountains, an engagement on the last summit. The spine draws the shooting
density along the way and the summit is its spike; chapters come from the
sessions and the places; the beat is proposed where the density and the
two people in frame agree, and confirmed with one key. A book of sixty is
walked in an evening, cut again a week later, and the twelve for a post is
the same pass over the sixty.

## The surfaces

One workspace stage, as Rank and the face wall are, reached from an album's
menu ("Tell it as a story") or from the sidebar under Albums.

**The spine** (left): chapters as rows, drawn to the density of shooting,
with their scene counts and place names, the beat marked, the gaps shown
as gaps. Click jumps; the arrows walk.

**The pass** (centre, the fast work): one scene at a time. The lead large;
the scene's other frames as a strip beneath, numbered. Every act one key:

| Key | Act |
|---|---|
| Enter | keep the lead, next scene |
| 1–9 | swap the lead for that frame, and keep |
| X | nothing from this scene |
| C | compare: Rank's pair mode on this scene's frames, back here when done |
| S / M | split the scene at the cursor / merge with the previous |
| B | this scene is a beat |
| Ctrl+Z | back one act |

A swap is a round (this over those, in this scene) and is written as one;
a keep is a round the fit already predicted. The fit learns the person's
leads from the first chapter and proposes better ones by the third.

**The second look**: when the pass ends, one screen shows the highest-rated
frame from every scene that gave nothing; one key admits. A pass that
cannot be checked is a pass a person will not trust.

**The distinctness guard**: when a proposed lead sits close in the space to
a frame already kept, anywhere in the story, the kept one is shown beside
it with one word, "alike", and the two keys that resolve it. Never a score;
the picture is the argument.

**The wall** (the slow work): every kept frame visible at once, in order,
as prints on a floor. Beside each, what the catalog knows of its shape and
tone, so the person can compose: wide against tall, colour against
black-and-white, a face turned toward against one turned away. Drag
reorders; chapters are the default order and the only order the machine
ever proposes. The count line reads against the budget and offers cuts in
the rule's order. **Cut again** runs the pass over the wall; the story
remembers where the person stopped and what round it is on.

**The contact sheet**: the wall exported as one sheet, numbered, for a
second pair of eyes anywhere.

**The cover**: proposed as the highest-rated wide frame in which everyone
in the story appears; one key to take it, or any frame on the wall.

## The same rule culls

A scene that has a lead has, by the same act, a rest. The pass offers to
deal with the rest, and only offers:

- **Keep N, reject the rest**, where N is the story's coverage rule or a
  count the person types; the rejects go to Trash as Rank's rejects do,
  undoable, never a byte touched.
- **Only where the fit is sure**: the lead's rating stands clear of the
  rest by more than the fit's own uncertainty about them. Twenty
  near-identical frames with one subtly better is this case; three good
  and different frames is not, and the pass says nothing.
- **Redundancy is likeness plus rating**: two frames the space calls the
  same subject, one rated above the other; the lower is offered as a cut
  with the pair shown.
- **The offer wears the tilde.** Across a whole story, "cull the rest of
  every sure scene" is one act at the end, shown first as a count and then
  done, with one Undo for all of it.

This is the cull the taste research asked for: relative within the
sequence, never an absolute "bad"; and it is the one that pays, because
every large shoot is mostly sequences.

## How the machine guides, and where it stops

- **It proposes, the person decides.** Every proposal wears the tilde until
  a key confirms it; every key is a decision in the log with a way back.
  Nothing is ever marked bad: a frame not chosen is a frame not chosen.
- **It learns inside the story**, from swaps, with the same head Rank uses.
  No knob, no strictness slider; the tilde is the only uncertainty shown.
- **It keeps count**: the budget and the guard are the two things a person
  loses track of across three hundred scenes and a machine never does.
- **It never sequences.** Time is the only order it offers; the person
  moves what should move. Google's Memories and Apple's Trips build the
  whole story unasked and cannot be corrected; that is the thing this is
  not.

## Learning from other programs

| Program | What it does well | What we take | What we leave |
|---|---|---|---|
| Lightroom Classic (Survey, Compare) | pairwise and small-group comparison | the pass as a keyboard rhythm | no notion of scene or sequence; every frame a peer (18) |
| Photo Mechanic | speed; a cull is a stream of keystrokes | one key per act, no waits | no help choosing (19) |
| Capture One (Sets, variants) | variants folded under one frame | the fold: a scene shows once | the fold is manual |
| Aftershoot, Narrative Select | scenes grouped; closed eyes and focus flagged | scene grouping by time and likeness; facets as facts | opaque scores; trust earned slowly and never total (20) |
| Google Photos, Apple Photos (Memories, Trips) | a story assembled from time, place and people | chapters from sessions and places | no hand on the wheel; no correction survives |
| The floor edit and the book dummy | the whole set seen at once; rounds over months | the wall; cut again; the contact sheet | nothing; this is the practice the design serves |

## Nothing extra

The owner, on a first list of nine additions: "those are interesting
gimmicks but i want elegance not occasionally useful bells". The test for
anything joining this design: it must be the one rule applied again, or a
practice the sources above name, never a feature beside them. Three items
of that list passed and are folded into the surfaces (the spine drawn to
shooting density, the second look, the post as the book's budget again);
the rest are below.

## Deliberate rejections

- **A quality score on every frame.** Deleted once already (the August
  scalar); facets are facts the fit weighs, never a number the person sees.
- **Automatic stories.** A story is opened, walked and finished by a person.
  The machine's whole job is to make the walk short.
- **A machine-proposed sequence.** Every editor in the sources calls
  sequencing composition and keeps it in human hands; the wall gives the
  person the materials (the frames, their shapes and tones, all at once)
  and nothing else.
- **A second curator inside the app.** The practice wants a second pair of
  eyes and the contact sheet provides one anywhere; a second author on the
  decision log is machinery for a rare case.
- **Bells.** Rhythm marks, factual captions, a place noticed across two
  stories, a story from a sentence: each occasionally useful, none the rule,
  every one a surface to maintain.
- **A layout tool.** Order and cover are the story's; pages and spreads are
  export's, later, from the same order.
- **A separate application.** Stories are albums; scenes are stacks the
  space widened; leads are rounds; chapters are sessions with places.

## Building it, in order

Each step is provable in the harness (with `--tiles` on the large
development library, which has bursts of five and forty-four film rolls
across eighty years) and lands as its own pull request.

1. **Scenes**: the cadence law's sets widened by likeness, as a query over a
   scope; a lead proposed by the fit. Provable on the fixture: fives become
   one scene each, the lead is the highest-rated.
2. **The pass** and the second look: the stage, the strip, the keys, the
   log. A story is an album with an order and a budget rule.
3. **The guard and the budget**: alike across kept leads; the count line;
   the offered cuts; coverage as a rule, and with it the cull.
4. **The wall, cut again, the contact sheet.**
5. **Chapters and the beat**: sessions with places on the spine; density
   and people propose the beat.
6. **Learning inside the story**: the story's rounds refit the head between
   chapters; measured as held-out accuracy on the person's own swaps,
   `scripts/sim_learn.py`'s way. The one step with a number to earn before
   it ships.
7. **The cover, and export in story order.**

## Sources

1. The Metropolitan Museum, press release for *The Americans* at fifty (2009); Blind Magazine on Frank's unseen frames.
2. Aperture, "The inside story of how Nan Goldin edited *The Ballad*" (Marvin Heiferman interview).
3. Heiferman, same interview: "Keep the pictures we couldn't live without, and then keep cutting from there."
4. Heiferman, same interview, on sequencing as music and on visual axes.
5. Gerhard Steidl, interviews with Scroll.in and the World Photography Organisation.
6. Michael Mack, 1854 Photography "Industry insights" and LensCulture.
9. Stephen Shore, photo-eye blog interview on *New York*: an order "that isn't somehow too specific, doesn't become narrative".
10. Todd Hido, Fotoroom and Behance interviews: pairs, then pairs of pairs; months with a dummy.
12. Dodho and Luminous Landscape on rhythm and variation.
13. Luminous Landscape, "Sequencing, part I": one frame per moment; a rereading pass that cuts repeats.
14. Digital Photography School and Fstoppers on National Geographic's ratios.
15. Shoot and Thrive; Selekt: wedding delivery norms.
16. Magnum Photos, "Creating a portfolio" (Meloni, Özmen); reached only through a search excerpt. ~
17. Fstoppers, "Why every photographer needs to delete 90% of their portfolio".
18. Glen Smith; Fstoppers on Lightroom's Survey view.
19. findme.photo, Photo Mechanic against Lightroom on culling speed.
20. PetaPixel on Aftershoot's 2026 promise; Aftershoot's culling FAQ.

Not verified: the grease pencil on Magnum contact sheets (Jay and Hurn, *On
Being a Photographer*, not reachable in excerpt); the final count of
*The Ballad*; Sally Mann's use of the gutter.
