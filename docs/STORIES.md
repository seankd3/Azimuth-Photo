# Stories — the design (2026-09-13)

Sean's ask, 09-12, in three parts:

> "next I want to design a dedicated workspace/page for album creation,
> curration and culling, where the top shots from the sequences are
> currated, so that every shot is differnt, distinct, and strong. I want the
> program to help guide this process and really help automate the process
> in a very context aware way."

> "I shot thousands of photos durring a long trip with my girlfriend we
> traveled all over the west coast, east coast, NYC, NH, NJ, and at the end
> we got engaged on mt washington, and I want tools in the software to help
> currate and put the story together. how can we build a bicycler for the
> photographer? wanting to help photographers tell the stories that lie in
> their archives."

> "I care more about the best shot in a seqence of 20 shots than I do
> having 20 shots from the same sequence even if I on average rank that
> seqence above another one"

This is the design. It follows `docs/ORGANIZING.md`'s law (predicted, then
earned) and `docs/product-vision.md`'s non-negotiables: one coherent
product, decisions durable, computations rebuildable, no weak intelligence
shipped. Nothing here is a second application; a story is an album that
knows its shape.

## The one rule

**A scene is worth its best frame, and a story is a sequence of scenes,
each shown once.**

Everything else follows. A frame's strength adds up across scenes and not
within one: the second-best of twenty near-identical frames is worth almost
nothing once the best is kept, however high it rates. So a set is judged by
the sum over scenes of each scene's best, never by the sum of ratings, and
scenes are compared by their best, never by their mean. The sequence Sean
worked hardest on cannot flood the set, and the one frame he had one chance
at cannot be outvoted by a burst.

Two kinds of round follow from it, both recorded in the decision log Rank
already writes:

- **Within a scene**: which frame leads. Rank's pair mode scoped to the
  scene is already this.
- **Across scenes**: which leads make the story, and in what order. Only
  ever one frame per scene is on the stage.

## What the archive already knows

The structure of a story is already in the catalog; today it is spread over
surfaces that do not talk to each other.

| Fact | Where it lives today | Used for |
|---|---|---|
| When | `date_taken`, sessions by capture gap (`web/library.py`) | chapters, the spine |
| Bursts | the cadence law, `web/stacks.py` (`around`, four on one beat) | the first cut at scenes |
| Likeness | the embedding space, `web/embed.py`, `web/rank.py`'s `_spread` | scenes wider than a beat; the distinctness guard |
| Strength | Plackett–Luce ratings from rounds, `web/rank.py` | the lead of a scene; the order of scenes |
| Technical facts | eyes open, subject sharpness, exposure (`web/sharpness.py`, `web/photostats.py`) | the lead, as facets the fit weighs (`docs/taste-and-culling-research.md`) |
| Who | People (`web/people.py`, `web/faces.py`) | chapters and beats: the two of them, the two of them together |
| Where | `web/places.py`: EXIF position, or a GPX track filled in by time | chapters: the coast, the city, the mountain |
| Gathering | Albums with rules, pins and exclusions (`web/model/sets.py`) | the story is one of these |
| Undo | the decision log, append-only | every act in the workspace has a way back |

Nothing new is inferred. The workspace arranges what is known and asks the
person the questions only the person can answer.

## The nouns

| Noun | What it is | Machine-made? |
|---|---|---|
| **Story** | An album with an order and chapters. Plain albums stay plain; a story is an album that has been through the pass. | no — the person opens one |
| **Chapter** | A run of scenes that share a session and a place (the coast; three days in the city; the mountain). | predicted (tilde) until the person accepts or redraws it |
| **Scene** | A run of frames on one beat, widened by likeness: the cadence law's set, merged with its neighbours when the space says they are the same subject. Twenty frames of her on the summit are one scene. | predicted; split and merge are decisions |
| **Lead** | The scene's frame in the story. Proposed by the fit; one keystroke to accept or swap. | predicted until accepted |
| **Beat** | A scene the story turns on. The engagement. Proposed from density (the scene shot most) and people (both present); confirmed by the person. | predicted until confirmed |
| **Budget** | How many frames the story is for: a book of sixty, a portfolio of twelve, a post of ten. Set once; the count line reads against it. | no |

Vocabulary is fixed by the canon: photographs, Rank, Best, Picked, Albums.
Story, Chapter, Scene, Lead are the new words and they are ordinary ones.

## The surfaces

One workspace stage, as Rank and the face wall are, reached from an album's
menu ("Tell it as a story") or from the sidebar under Albums.

**The spine** (left): chapters as rows with their scene counts and place
names, the beat marked. Click jumps; the arrows walk.

**The pass** (centre, the work): one scene at a time. The lead large; the
scene's other frames as a strip beneath, numbered. Every act one key:

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
leads from the first chapter and proposes better ones by the third — the
same head as Rank, seeing the same log.

**The distinctness guard**: when the proposed lead sits close in the space
to a frame already kept, the strip shows the kept one beside it with one
word, "alike", and the two keys that resolve it: keep both, or this one
instead. Never a score; the picture is the argument.

**The wall** (below, or a second view): the story so far in order, each
scene once, the count line reading "23 of 60 · 2 alike". Drag reorders;
chapters are the natural order and the default. Over budget, the wall
offers the cuts in the order the rule gives them: the weakest lead of the
most-represented chapter first, then the closer of any alike pair.

**The cover**: proposed as the highest-rated wide frame in which everyone in
the story appears; one key to take it, or any frame on the wall.

**Search stays search**: "engagement" typed into the box finds the summit
through the meaning search; the beat is one keystroke from there.

## How the machine guides, and where it stops

- **It proposes, the person decides.** Every proposal wears the tilde until
  a key confirms it; every key is a decision in the log with a way back.
  Nothing is ever marked bad: a frame not chosen is a frame not chosen.
- **It learns inside the story.** Leads swapped in the first chapter reshape
  the leads proposed in the next: the fit is the same Plackett–Luce head
  with the same facets, refit on the story's own rounds. No knob, no
  strictness slider; the tilde is the only uncertainty the person sees.
- **It keeps count.** The budget and the distinctness guard are the two
  things a person loses track of across three hundred scenes and the two
  things a machine never does.
- **It never sequences for you.** Chapters propose the order because time
  does; the person moves what should move. Google's Memories and Apple's
  Trips build the whole story unasked and cannot be corrected; that is the
  thing this is not.

## Learning from other programs

| Program | What it does well | What we take | What we leave |
|---|---|---|---|
| Lightroom Classic (Survey, Compare) | N-up comparison, reject by key | the pass as a keyboard rhythm | no notion of scene; every frame is a peer |
| Photo Mechanic | speed; a cull is a stream of keystrokes | one key per act, no waits | no help choosing |
| Capture One (Sets, variants) | variants folded under one frame | the fold: a scene shows once | the fold is manual |
| Aftershoot, Narrative Select | scenes grouped; closed eyes and focus flagged; "close-ups" as a group | scene grouping by time and likeness; facets as facts | absolute verdicts, scores, sliders; the trust failures `docs/taste-and-culling-research.md` records |
| Google Photos, Apple Photos (Memories, Trips) | a story assembled from time, place and people | chapters from sessions and places; the beat | no hand on the wheel; no correction survives |
| Magnum's contact sheets, the photobook editors' rule | one beat per spread; a rhythm of wide, medium, tight | the wall as a contact sheet; the cover rule | layout, which is export's job |

The gap in the market is the same one `docs/ORGANIZING.md` found: no tool
lets the person correct the machine and have the machine learn from it.
Here the correction is a swap, and the learning is the next chapter's leads.

## Deliberate rejections

- **A quality score on every frame.** Deleted once already (the August
  scalar); facets are facts the fit weighs, never a number the person sees.
- **Automatic stories.** A story is opened, walked and finished by a person.
  The machine's whole job is to make the walk short.
- **A layout tool.** Order and cover are the story's; pages and spreads are
  export's, later, from the same order.
- **A separate application.** Stories are albums; scenes are stacks the
  space widened; leads are rounds; chapters are sessions with places. The
  workspace is one stage and one set of keys.

## Building it, in order

Each step is provable in the harness (with `--tiles` on the large
development library, which has bursts of five and forty-four film rolls
across eighty years: a story of a century) and lands as its own pull
request.

1. **Scenes**: the cadence law's sets widened by likeness, as a query over a
   scope; a lead proposed by the fit. Provable on the fixture: fives become
   one scene each, and the lead is the highest-rated.
2. **The pass**: the stage, the strip, the keys, the log. A story is an
   album with an order.
3. **The guard and the budget**: alike across kept leads; the count line;
   the offered cuts.
4. **Chapters and the beat**: sessions with places on the spine; density
   and people propose the beat.
5. **Learning inside the story**: the story's rounds refit the head between
   chapters; measured as the fit's held-out accuracy on the person's own
   swaps, `scripts/sim_learn.py`'s way.
6. **The cover, and export in story order.**

Step 1 needs nothing the engine lacks. Step 5 is the one with a number to
earn before it ships (the "weak intelligence does not ship" rule).
