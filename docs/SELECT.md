# Select — the design (2026-09-13, second draft)

Photographers call it the edit, or the selects: from thousands of frames,
the few dozen that will be shown, each different, distinct and strong, in
an order that says something. A wedding to deliver, a trip to remember, a
year to review, a body of work to show, one person across a decade. The
act is the same every time; only the shape differs. This is the workspace
for that act, for any photographer. It replaces the first draft (Stories)
after a sourced study of how the best photographers and their editors
actually work; the owner's statements are in `MASTER_PLAN.md` §1.20.

It keeps `docs/ORGANIZING.md`'s law (predicted, then earned) and
`docs/product-vision.md`'s non-negotiables. A select is an album that has
been edited; nothing here is a second application.

## First principles

The job is reduction under a person's taste. Taste cannot be computed;
almost everything around it can. Seven principles follow, and each decides
something below.

1. **The machine removes what is the same; the person chooses among what
   is different.** Redundancy is computable from time and likeness.
   Preference is not. Every screen shows only things that differ and asks
   one question about them.
2. **Judgment is comparative, never absolute.** A person is good at "this
   or that, large, side by side" and poor at "rate this". No screen asks
   for a score.
3. **Attention is the scarce resource, so ask only what the machine cannot
   answer.** Where the fit is sure of a scene's lead and the rest is
   redundant, the scene is settled without a question, wearing the tilde,
   visible on the wall and reversible. The pass asks in order of doubt and
   says how much is left: "42 scenes need you; 258 are settled ~". Three
   thousand frames become an hour of real decisions.
4. **The machine remembers, counts and notices; the person composes.**
   Memory across hundreds of scenes, the budget, and "this is like one you
   kept" are what a person loses and a machine never does. Order, rhythm
   and meaning are the reverse.
5. **The log is the only state.** Every proposal is derived; every act is a
   decision with a way back. A select can always be recomputed and a round
   replayed; nothing is managed.
6. **Never wait, never rush.** The pass is instant. The wall is slow on
   purpose.
7. **Three settings, ever.** Scope, budget rule, N. No modes, no sliders,
   no strictness; the tilde is the only uncertainty shown.

The powerful part and the simple part are the same thing: the app spends
a person's attention only where it is worth something.

## The one rule

**A scene is worth its best frame, and a select shows each scene once.**

A frame's strength adds up across scenes and not within one: the second
best of twenty near-identical frames is worth almost nothing once the best
is kept. A select is judged by the sum over scenes of each scene's best,
never by the sum of ratings; scenes are compared by their best, never by
their mean. The sequence a photographer worked hardest on cannot flood the
set; the frame they had one chance at cannot be outvoted by a burst.

## What the practice says, and what it decides

Sourced (list at the end; ~ marks a claim not confirmed against a primary
source). Each line of practice decides one thing here.

| The practice | Decides |
|---|---|
| One frame per moment; fourteen frames of one moment become one in the book (13) | the one rule |
| The edit is made in rounds with time between: 27,000 frames to 1,000 work prints to 83 over a year (1); weeks on an editor's floor (2) | a select is a sequence of rounds; every round is kept and any can be returned to |
| "Keep the pictures we couldn't live without, and then keep cutting from there" (3) | the budget is a target the count line reads against, and the offered cuts come in the rule's order |
| The whole set is seen at once, as prints on a floor (2) | the wall: every kept frame visible together, moved by hand |
| A second pair of eyes is structural; the maker is the worst judge of their darlings (2, 5, 6) | the contact sheet: the wall as one numbered sheet, for anyone, anywhere |
| Sequence is composition, not narration: horizontal against vertical, wide against tight, gaze toward against away (4, 9, 10) | the wall shows each frame's shape and tone; the machine offers no order but time |
| Repetition without variation flattens a sequence however strong each frame (12) | the guard works across the whole select, not only inside a scene |
| Delete far more than feels comfortable; fewer in front of a viewer beats more (15, 16, 17) | default budgets from practice: twenty for a portfolio, sixty to eighty for a book, fifty to a hundred an hour for a delivery |
| Speed at the first pass, patience at the sequence (10, 19) | two rooms: the pass, one key per act and never a wait; the wall, slow on purpose and remembered |
| Machine picks are trusted only when visible, reversible and learned from the person's own choices (20; `docs/taste-and-culling-research.md`) | every proposal wears the tilde until a key confirms it; rejects go to Trash; the fit learns from swaps |
| The final sequence is never delegated (2, 6, 10) | the machine proposes leads and keeps count; the order is the person's |

Numbers the defaults come from:

| Context | Shot | Kept | Source |
|---|---|---|---|
| *The Americans* | ~27,000 | 83 | (1) |
| A National Geographic story | 30,000–40,000 | 10–20 | (14) |
| A full-day wedding | 1,500–3,000 | 400–800 | (15) |
| A portfolio review | a body of work | 20–30 shown | (16) ~ |

## Four nouns

| Noun | What it is | Machine-made? |
|---|---|---|
| **Select** | An album with a scope (a chip sentence, the language every surface already speaks), a budget rule, and its rounds. Plain albums stay plain; a select is an album that has been edited. | no — the person opens one |
| **Scene** | In the first round, a run of frames on one beat widened by likeness (the cadence law's set, merged with its neighbours when the space says they are the same subject). In later rounds, a group of kept frames the space calls alike. | predicted; split and merge are decisions |
| **Lead** | The scene's frame in the select, proposed by the fit, accepted or swapped with one key. | predicted until accepted |
| **Round** | One pass over the select's scenes, kept whole. Round one edits the shoot; round two edits round one's wall; and so on until the person stops. | no — the person starts one |

Chapters are sessions with places, which the app already has; a beat is a
scene the person marks, not a noun. The vocabulary of the canon is kept
(photographs, Rank, Best, Picked, Albums) and four ordinary words join it.

## Two rooms

**The pass** is the fast room. It asks in order of doubt: the scenes
whose lead the fit is unsure of, or whose frames the space calls different,
come first; scenes whose lead stands clear of a redundant rest are settled
without a question, wearing the tilde, and appear on the wall like any
other kept frame, one key to reopen. The count line says how much needs a
person ("42 scenes need you; 258 are settled ~"), and the pass ends when
the doubt does, not when the scenes do. One scene at a time: the lead
large, the scene's other frames as a numbered strip beneath, and, when
the scene has faces, a face row: every detected face at once with its eyes
and sharpness read, which the faces and sharpness passes already produce,
so a group is judged without zooming (the one AI-era surface every
reviewer of every tool praises; `docs/culling-market-research.md`). Every
act one key, no act waits.

| Key | Act |
|---|---|
| Enter | keep the lead, next scene |
| 1–9 | swap the lead for that frame, and keep |
| X | nothing from this scene |
| C | compare: Rank's pair mode on this scene, back here when done |
| S / M | split the scene at the cursor / merge with the previous |
| B | mark this scene as a beat |
| Ctrl+Z | back one act |

A swap is a round in Rank's sense (this over those, in this scene) and is
written to the same log; a keep is a round the fit predicted. The fit
learns the person's leads from the first chapter and proposes better ones
by the third. When a proposed lead sits close in the space to a frame
already kept anywhere in the select, the kept one appears beside it with
one word, "alike", and the two keys that resolve it: the guard.

When the pass ends, **the second look**: one screen of the highest-rated
frame from every scene that gave nothing, one key to admit each. A pass
that cannot be checked is a pass a person will not trust.

**The wall** is the slow room. Every kept frame at once, in order, as
prints on a floor; the chapters along its left margin, drawn to the
density of shooting, the beats marked, the gaps shown as gaps. Beside each
frame, what the catalog knows of its shape and tone (wide or tall, colour
or black and white, a face toward or away), so the person can compose.
Drag reorders; time is the default order and the only one the machine
ever offers. The count line reads against the budget ("312 of 4,000 ·
every scene · 6 alike") and, over it, offers cuts in the rule's order:
the weakest lead of the most-represented chapter, then the closer of any
alike pair.

**Cut again** starts the next round: the wall becomes the pool, alike
groups become the scenes, and the pass runs over them. The select remembers
which round it is on and where the person stopped. Every round is kept;
"back a round" is one act.

**The contact sheet** is the wall as one numbered sheet, exported for a
second pair of eyes anywhere. **The cover** is proposed as the highest-rated
wide frame in which everyone in the select appears, one key to take it.

## Shapes

Every album a photographer makes is the same two rooms over a different
scope with a different budget rule. None needs a room the others lack;
the test of the design is that none ever will.

| Shape | Scope | Budget rule | What matters most |
|---|---|---|---|
| **Project** (a trip, a series) | a span of time | a number: sixty to eighty for a book | the beats, the pulse along the margin |
| **Delivery** (a wedding, a job) | a day, or a client's chips | coverage: every scene represented, N per scene growing with its length (eighty frames of the first dance deliver five, three of the rings deliver one); fifty to a hundred an hour | the second look, so nothing is missed; then cull the rest in one act; export in order |
| **Review** (a year) | a year | a floor per month, the rest by density | the guard across the year |
| **Portfolio** (a body of work) | everything, or one label | twenty to thirty, by hand; the guard is the instrument | across-scene rounds in Rank's tournament mode, where the leaders meet |
| **Subject** (a person, a label) | one chip | a number or coverage | a smart album that has been edited; new matches arrive as unedited scenes |

Three budget rules in all (a number, a floor per chapter, coverage), one
chosen when the select is opened and shown on the count line. A smart
album is a select that has not been edited yet; what it does as new
photographs match its rule is the standing question below.

**A worked example.** A long trip: two coasts, a city, three days in the
mountains, an engagement on the last summit. Round one walks a few hundred
scenes in an evening; the margin's density spikes at the summit and the
beat is confirmed with one key. The wall holds seventy; a week later, cut
again brings it to sixty for the book; the twelve for a post is a third
round over the sixty.

## What a scene is, in each kind of work

Only two things vary between kinds of work: what a scene is, and what
makes a lead. The fit weighs facets differently per select because the
swaps teach it; no one names the difference.

| Work | A scene is | A lead is | Budget |
|---|---|---|---|
| Wedding, event | a run by cadence and by who is in frame | eyes open, faces sharp, the moment | coverage; a same-day peek is the same select with a budget of ten |
| Portrait session | a pose, found by likeness more than time | expression, eye sharpness | one per pose |
| Sport, wildlife, children | a play, a tight burst | subject sharp at the peak | one per play; the cull that pays most |
| Product, interiors | a setup or a room, pure likeness | sharpness and exposure | one per setup, always |
| Street, landscape | mostly loners | the frame itself | the pass degenerates gracefully into Rank across singles; the guard works across the body |
| A family across decades | the same people at the same events, years apart | as the person swaps | a person's life, scope one chip |
| Client proofing | as the delivery | as the client marks | the contact sheet goes out numbered; the marks come back as decisions with the client as author, which the log already allows |

## How the machine earns its place

Sean, 09-13: "how can we best leverage the computer and cutting edge
software to best serve users?"

The fashionable answer is more model. The useful answer is six older
ideas, and one test for when a model is the right tool at all.

**Do what is cheap for a machine and dear for a person.** Counting,
remembering, exhaustive comparison, vigilance that never tires. A person
laying sixty prints on a floor cannot see every pair; six hundred cannot be
laid out at all. The machine compares every kept frame to every other and
never gets bored at frame two thousand. It should never be asked to judge,
which is cheap for the person and impossible for it.

**Move every cost to before the sitting.** The largest lever in this app is
not a model, it is the scheduler. Scenes, leads, likeness and facets are
made on the chore lane the night a card is imported, as tiles already are.
When the person opens the pass, the doubtful scenes are ready and the sure
ones are settled under the tilde. Nobody watches it think.

**Latency is not a metric, it is whether a thought survives.** Under a
tenth of a second the person's attention stays on the photograph; over a
second it moves to the tool, and the edit becomes software operation. This
is why Photo Mechanic, which has no intelligence of any kind, still beats
every clever thing in the field for the first pass
(`docs/culling-market-research.md`). Being instant is the intelligence a
person feels.

**Every decision must make the next one better, forever.** Most software
treats a sitting as disposable: the picks are an output, and next month
starts from nothing. Here the rounds are the asset and everything else is
derived. The head that learned a person's leads last month proposes this
month's; a select's swaps sharpen it inside the select; ten years of
sittings compound into something no new tool can hand anyone. This is the
whole return on the work, and it exists only because decisions are kept as
decisions rather than as scores.

**Decisions durable, computations rebuildable** is also how the app stays
current without churn. A better encoder arrives next year: swap it, refit
the same rounds against the new space, and every past judgement still
counts. Had the app stored scores instead of rounds, it could never take
the upgrade without losing the person's history. Being able to replace the
model is worth more than having the newest one.

**Local is no longer a trade.** The encoder, the faces and the sharpness
read all fit on a modest card; privacy costs nothing now, and the research
shows client RAWs in the cloud is a live worry across the field. Nothing
leaves the machine.

### The test for a model

Does the question belong to the photograph or to the person?

| Question | Belongs to | Answered by |
|---|---|---|
| What is in this frame? Who? Where? | the photograph | a model, well |
| Is it sharp, are the eyes open, is it exposed? | the photograph | a model, well |
| What is this frame like? Which frames are the same subject? | the photograph | the embedding space |
| Which of these is better? What is worth keeping? | the person | the rounds, only |
| What should come after what? | the person | nobody but them |

Every tool that answered a person-question with a model produced the
field's universal complaint, the false reject of a keeper. The four places
image understanding earns its keep here are all photograph-questions:
making a scene a subject rather than a burst, distinctness across a whole
select, the shape of what is kept (shown as the frames themselves, grouped:
"fourteen of these twenty are the same golden field"), and coverage against
a list's words. The space knows what a photograph is of and what it is
like. It never knows what it is worth.

## The list

Deliveries have a shot list; every working photographer writes one. A
select holds it as plain phrases ("rings", "first dance", "the
grandmother"), each a meaning search the app already has, and the wall
shows which lines have a kept frame and which have none. Search reused,
the practice itself, and coverage turned from a count into the answer a
client will ask for. It joins because it is the rule applied to a list
the person already keeps, not a feature beside it.

## Where a comparison pays: the boundary

Sean, 09-13: "does our ranking mode improve the efficiency of this sort of
work?"

Honestly: for a delivery, no, and it would make it slower. Rank produces an
ordering, and an ordering costs many rounds per photograph to settle. A
delivery does not need an order. It needs one decision per scene against a
threshold, which is one pass. Spending Rank rounds on three thousand frames
to learn that frame 1,400 outranks frame 1,900, when both are delivered and
neither is ever looked at again, is work with no answer attached.

For a portfolio, the opposite: comparison is the only instrument that
works. Twenty from a lifetime cannot be chosen by a threshold, because no
absolute score is trustworthy at that precision, and the practice agrees
(the sources reject "rate this" and keep "this or that"). There the order
*is* the product.

So the rule, which is decision theory and not a preference:

**A comparison is worth exactly what it changes. Spend rounds at the
boundary.**

Every select has a boundary: the budget's last slot. Frames far above it
are kept whatever happens; frames far below it are out whatever happens;
comparisons among either are information the select cannot use. The frames
that straddle the line are the only ones whose order decides anything, and
the fit already knows which they are: it scores every photograph and says
how unsure it is of each (`fitted` in `web/rank.py` returns both).

This gives Rank one new mode and makes it the engine of the whole
workspace rather than a game beside it:

| Mode | Seats | Serves |
|---|---|---|
| `learn` (today) | where the fit is most unsure | taste, before any select exists |
| `tournament` (today) | the highest-rated judged frames | finding the very top |
| **`bubble`** (new) | the frames whose scores straddle the budget's last slot, most unsure first | every select, at the only place an answer changes |

The bubble is a scope, not a strategy: `candidates(..., mode="bubble")`
over the select's kept-and-near-kept band. A round from it is a round in
the same log, fitted by the same head. Nothing else about Rank changes.

What this buys, by shape:

- **A delivery** spends no rounds at all by default. The pass settles it;
  the bubble is offered only when the count is over budget and the person
  wants the cut decided rather than taken by rule.
- **A portfolio** is almost entirely bubble rounds: twenty slots, a
  thousand candidates, and every round is a duel at the line. This is the
  one shape where Rank *is* the workspace.
- **A subject album or a review** sits between: a pass for redundancy,
  then a handful of bubble rounds where the floor per chapter bites.

## A select is a standing question

Sean, 09-13: "cultivating regular commercial work, and reflective lifetime
portfolios, and subject based currated albums... thinking about the
dynamic/smart collections, what's the optimal shape here?"

The shape is one spectrum, not three kinds of thing. Membership can be
decided by hand, by rule, by rule with exceptions (`docs/ORGANIZING.md`'s
pins and exclusions), or by rule and then edited down. That last one is a
select. So:

**A select is a smart album that has been edited, and it stays alive.**

Its scope is a rule, so new photographs match it as they arrive. What
arrives does not silently join the kept set and does not silently vanish:
it arrives at the boundary. A new frame whose score clears the weakest
keeper is **a challenger**, and the select says so quietly in the sidebar
("Portfolio · 1 challenger"). One key opens a duel between the challenger
and the frame it threatens; the winner is kept, and the round is a round.
That is the whole mechanism, and it is the same boundary again.

This is what separates the three kinds of work without three designs:

| Work | Its boundary | After the first pass |
|---|---|---|
| **Commercial delivery** | coverage, every scene represented | finished; archived with the job. Challengers are meaningless once delivered, so a delivered select is closed and says so |
| **Lifetime portfolio** | the twentieth slot | never finished. Standing forever, challengers arrive with every shoot, the fit sharpens with every duel |
| **Subject album** | a number or coverage | standing, refreshed as the person or label gains photographs; unwalked scenes queue at the end of the spine |

One noun, one pass, one boundary, three lifetimes. A delivery closes, a
portfolio never does, a subject album waits.

## The same rule culls

A scene that has a lead has, by the same act, a rest. The pass offers to
deal with the rest, and only where the fit is sure: the lead's rating
stands clear of the rest by more than the fit's own uncertainty, or two
frames the space calls the same subject differ in rating. Keep N, reject
the rest: to Trash, as Rank's rejects go, undoable, never a byte touched.
Across a select, "cull the rest of every sure scene" is one act shown
first as a count, then done with one Undo. Relative within the sequence,
never an absolute "bad"; and the cull that pays, because every large shoot
is mostly sequences.

## Where the machine stops

It proposes leads and wears the tilde until a key. It keeps count against
the budget and watches for alike across the whole select. It learns from
swaps with the head Rank already has. It never orders beyond time, never
marks a frame bad, never finishes a select. Google's Memories and Apple's
Trips build the whole thing unasked and cannot be corrected; the floor edit
and the book dummy are the practice this serves instead.

## What the archive already knows

| Fact | Where it lives | Used for |
|---|---|---|
| When; sessions by capture gap | `date_taken`, `web/library.py` | chapters, the margin |
| Bursts | the cadence law, `web/stacks.py` | first-round scenes |
| Likeness | `web/embed.py`, `web/rank.py`'s `_spread` | wider scenes; later-round scenes; the guard |
| Strength | Plackett–Luce ratings, `web/rank.py` | the lead; tournament rounds among leads |
| Facets | eyes, subject sharpness, exposure, orientation, colour (`web/sharpness.py`, `web/photostats.py`) | the lead's fit; the wall's shape and tone |
| Who, where | `web/people.py`, `web/places.py` | chapters, beats, coverage |
| Gathering, undo | `web/model/sets.py`, the decision log | a select is an album; every act has a way back |

Nothing new is inferred.

## What the market says

Five sourced research passes over the culling tools and their users are
digested in `docs/culling-market-research.md`. What they settle here:
never mark a frame bad (the universal complaint is the false reject of a
keeper); sell the redundancy pass, not the judgment pass (a working
reviewer measured Aftershoot's saving at "5 to 10 percent" because "it
does not understand where the emotion is"); the second look is how every
tool is actually used; the speed of the pass is the first praise and the
first complaint of every tool; one flag and one reject, no stars, is what
both hand methods teach; marks must never vanish (Lightroom's silently
reverting stars are a top complaint); local, always; and learning which
frame a person keeps is the empty seat in the whole table. Two changes to
this design came from it: the face row in the pass, and client marks
returning as decisions.

## Deliberate rejections

- **A quality score on every frame**: deleted once already; facets are facts
  the fit weighs, never a number the person sees.
- **A machine-proposed sequence**: every editor in the sources calls it
  composition and keeps it in human hands.
- **Automatic selects**: opened, walked and finished by a person; the
  machine's job is to make the walk short.
- **A second curator inside the app**: the contact sheet gives a second
  pair of eyes anywhere. Its numbers coming back as decisions with the
  client as author is not this: it is a plain import of marks, the
  practice's missing half (every proofing platform is one-way), and the
  log already allows it.
- **Bells**: rhythm marks, captions, a place noticed across two selects, a
  select from a sentence. Each occasionally useful, none the rule, each a
  surface to maintain (the owner, 09-13: "elegance not occasionally useful
  bells").
- **A layout tool**: order and cover belong here; pages and spreads to
  export, later, from the same order.
- **A third room**: the first draft had a spine as its own surface; it is
  the wall's margin.

## Building it, in order

Each step is provable in the harness (`--tiles` on the large development
library: bursts of five and forty-four film rolls across eighty years) and
lands as its own pull request.

1. **Scenes and leads**: the cadence law's sets widened by likeness over a
   scope; a lead by the fit. Fives on the fixture become one scene each.
2. **The pass and the second look**: a select is an album with a budget
   rule and a round.
3. **The guard and the budget**: alike across the select, the count line,
   the offered cuts, coverage, and with it the cull. The budget defines the
   boundary everything after this leans on.
4. **The wall, cut again, the contact sheet.**
5. **Chapters and beats** on the margin.
6. **Learning inside the select**: the head refit between chapters,
   measured as held-out accuracy on the person's own swaps
   (`scripts/sim_learn.py`'s way). The one step with a number to earn.
7. **The bubble**: `candidates(mode="bubble")` seats the frames straddling
   the budget's last slot, most unsure first. Measurable before it ships,
   `scripts/sim_learn.py`'s way: rounds spent at the boundary against rounds
   spent by `learn`, scored on how often the final kept set changes. A mode
   that does not change the set faster than the one it replaces does not
   ship.
8. **The standing select**: a challenger at the boundary when new work
   matches the scope, one key to duel it against the frame it threatens; a
   delivery closes instead.
9. **The cover, and export in order.**

## Sources

1. The Metropolitan Museum, press release for *The Americans* at fifty (2009); Blind Magazine on Frank's unseen frames.
2. Aperture, "The inside story of how Nan Goldin edited *The Ballad*" (Marvin Heiferman).
3. Heiferman, same: "Keep the pictures we couldn't live without, and then keep cutting from there."
4. Heiferman, same, on sequencing as music and on visual axes.
5. Gerhard Steidl, interviews with Scroll.in and the World Photography Organisation.
6. Michael Mack, 1854 Photography and LensCulture.
9. Stephen Shore, photo-eye interview on *New York*: an order that "doesn't become narrative".
10. Todd Hido, Fotoroom and Behance: pairs, then pairs of pairs; months with a dummy.
12. Dodho and Luminous Landscape on rhythm and variation.
13. Luminous Landscape, "Sequencing, part I": one frame per moment.
14. Digital Photography School and Fstoppers on National Geographic's ratios.
15. Shoot and Thrive; Selekt: wedding delivery norms.
16. Magnum Photos, "Creating a portfolio" (Meloni, Özmen), via a search excerpt only. ~
17. Fstoppers, "Why every photographer needs to delete 90% of their portfolio".
19. findme.photo, Photo Mechanic against Lightroom on culling speed.
20. PetaPixel on Aftershoot's 2026 promise; Aftershoot's culling FAQ.

Not verified: the grease pencil on Magnum contact sheets (Jay and Hurn); the
final count of *The Ballad*; Sally Mann's use of the gutter.
