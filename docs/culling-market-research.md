# Culling and selection tools: the market and what its users say (2026-09-13)

Sean's asks, 09-13: "do a deep dive on aftershoot, aswell as peoples
opinions and feedback on aftershoot and other apps. lets gather as much
data on this as possible" and "lets do a shitload of market research for
all culling programs and their users feedback." Five sourced research
passes ran in parallel (Aftershoot; the other AI cullers; how professionals
cull by hand and hand off to clients; the organisers and platform apps with
Google's and Apple's own writing; the market and the sentiment at scale).
This is the digest. Every number carries its source; ~ marks a claim that
could not be confirmed against a primary source, and vendor benchmarks are
named as such. What it decides for the design is in `docs/SELECT.md`.

**How the community voice was gathered.** Reddit itself answers 403 from
this container, but the Arctic Shift archive serves the same comments. Two
traps on the way: a page of 50 exceeds the archive's own query timeout and
fails with a message that says "slow down", which sends you to the wrong
fix (25 returns in six seconds); and a rare term in a large subreddit can
still exceed it however small the page, so a handful of combinations
return nothing at all. 296 unique comments were read, 286 substantive,
dated 2026-01 to 2026-09, from r/WeddingPhotography (117), r/Lightroom
(71), r/photography (47), r/AskPhotography (25), r/sportsphotography (25)
and r/photographer (1), on the terms aftershoot, cull, culling and photo
mechanic. The DPReview forum bodies are still unread. What that material
says is in "What photographers say, in their own words" below, and it
**contradicts** one of this document's conclusions, which is recorded
there rather than quietly corrected.

## The tools

| Tool | Runs | Groups scenes | Judges | Learns the person | Review model | Price | Chief praise | Chief complaint |
|---|---|---|---|---|---|---|---|---|
| Aftershoot | local desktop | bursts by likeness, strictness slider | blur, closed eyes, exposure, "Key Faces" panel | edit profile from past catalogs (3–4 shoots); culling profile ~ | rejects kept in buckets (Duplicates, Closed Eyes, Blurred); Spray Can paint-reject; keys P/X/1–5 | $10–45/mo flat | speed, near-match to own picks, support (Trustpilot 4.9, 1,766) | misses emotion and composition; export weak; CPU-bound |
| Narrative Select | Mac only | scenes; "Distill" auto-reject with strength | focus 0–100, eye state, face close-ups panel | no | keyboard, instant switching, colour ranks with reasons | free tier; $10–79/mo | "ridiculously fast"; the face panel for groups | Mac only; Distill over-rejects emotional closed-eye frames |
| Imagen (Select) | cloud | bursts | blur, blink, kiss; claims intentional-blur awareness | edit style from own edits | inside Lightroom's flags and stars | $12–18/mo cull; $0.05/photo edit | "95–98%" (Trustpilot) ~ | per-image bill shock ~; culling secondary; 63% in a rival's test (vendor) ~ |
| FilterPixel | cloud | autogroup duplicates | exposure, composition, blink; "Deep Cull" emotion (2026) | no | web review | ~$15–19/mo | speed; support (Trustpilot 4.8, 174) | export hitches; missed shots; its own accuracy claims (94.7% / 96.1%) are vendor ~ |
| Optyx | local + cloud AI | Autogroup, Autocull profiles per shoot type | expression, sharpness, exposure | profiles, not learning | Lightroom Classic plugin | ~$10–15/mo (sources conflict) ~ | RAW previews "like JPEGs"; local | "marks an eye-closed shot as best"; weak on unconventional frames |
| OptiCull | Mac, local | similarity thresholds | blur, blink, synced face zoom | no | export to Lightroom and Capture One | free; $59.99 Pro lifetime | fast local RAW | thin independent coverage ~ |
| Lightroom Assisted Culling | in Classic | stacks by time and likeness, thresholds | per-face eyes and focus, "Face View" | no | native flags | in the subscription | free, per-face granularity praised (PetaPixel) | "very slow"; "stops after less than 1000"; sharp frames rejected; crashes (Adobe Community) |
| Photo Mechanic | local | none | none | no | embedded-JPEG browse, keys, IPTC | one-time / sub | speed: "nothing else works as fast or as reliably" | no help choosing; dated UI; embedded JPEG unreliable for critical focus |
| Capture One | local | variants, Sets | none | no | Sessions, Selects folder, keyboard | in the licence | keyboard-first, capture to cull to edit | fewer guides; some tags need shortcuts assigned |
| Excire Foto / Search | local (+ LrC plugin) | near-duplicates, strictness | best-shot score, per-face sharpness | no | grid overrides | licence | "the locally running AI does a pretty good job" | "can miss some photos"; claims over Lightroom are its own ~ |
| Peakto | Mac, local | dedup across catalogs (v2.6) | aesthetic and technical scores | no | meta-catalog | licence | NAB 2025 product of the year | user complaints not found; coverage is trade press ~ |
| Mylio Photos+ | local, multi-device | bursts by time window | auto-marks a best | no | Quick Review before delete | ~$99/yr | no mandatory cloud | not culling-focused |
| Apple Photos | on device | duplicates merge; Memories | "keeps the highest quality" | no | per-pair confirm | free | Memories from people, places, events | the highest-quality claim "found untrue in spot tests" (MacRumors); RAW+JPEG merged blind |
| Google Photos | on device / cloud | Top Shot (90 frames, 1.5 s), Best Take composites, Highlights | optical flow, exposure, gyro, smiles, eyes (Top Shot paper) | no | swap, hide | free | Top Shot's documented pipeline | "prioritises inanimate objects over humans"; Best Take "I'll keep my terrible family photos"; Memories demoted to Moments 2025 |
| Immich / PhotoPrism | self-hosted | Immich: ML similarity groups; PhotoPrism: checksum only | none / faces | no | review then approve | free | privacy; "VERY impressed" | face false positives ~; no best-shot |

Nobody in the table learns from a correction to the *selection*. Aftershoot
and Imagen learn an editing style; none learns which frame a person keeps.
That gap is the one `docs/ORGANIZING.md` found for labels and people, and
it is the one Select is built on.

## What photographers say, across every tool

Ranked by how many independent sources carried the theme.

**Complaints**

1. **The false reject of a keeper** (every AI tool): shallow depth of field, intentional motion blur, a small subject in blurry foreground, a deliberately closed eye, a squint in bright light. "Its idea of composition is totally whack" (Fstoppers, Aftershoot). Distill "may reject images with closed eyes that carry emotional value" (Fstoppers, Narrative). "It marks an eye-closed shot as the best shot" (Fstoppers, Optyx). "Very sharp images are rejected" (Adobe Community, Lightroom). 7+ sources — all of them reviews and support threads; see the contradiction under "in their own words", where photographers talking to each other never raise it.
2. **It does not know where the emotion is.** A wedding reviewer on Aftershoot: time saved "around 5 to 10 percent, not more"; it "does not pick the best frame, does not understand where the emotion is" (Trustpilot, Aug 2026). The judgment pass is not the pass the tools save. 3 sources.
3. **Slowness and hardware dependence**: Lightroom's own culling "very slow", stopping "after analysing less than 1,000"; Aftershoot "highly dependent on number of CPU cores"; Lightroom previews "render in minutes, not split seconds"; Bridge "unusable" after version 12. 6 sources.
4. **Marks that vanish**: stars and flags silently reverting to zero across Lightroom catalog operations (Adobe Community, Lightroom Queen). 3 sources.
5. **Per-image pricing shock** (Imagen): "$400 for that month" ~. 2 sources, not directly verified.
6. **Platform limits**: Mac-only (Narrative, OptiCull). 3 sources.
7. **Weak outside people**: landscape, travel, architecture, wildlife bursts. 3 sources.
8. **Automatic curation picks the wrong file or the dull moment** (Apple, Google): the merge that keeps the lower resolution; the recap that "should be able to recognise when a photo is out of focus". 4 sources.

**Praise**

1. **Speed of the pass**: instant switching, no render wait, "by the time I had made coffee", "culling an entire wedding of 1,500 in well under 10 minutes" (a keyboard-only method, Shotkit ~). 8+ sources; the single most consistent praise for any tool, AI or not.
2. **A high match to the photographer's own picks** on people-heavy work: "almost exactly the ones I would have picked myself" (Shotkit, ~98% on 156 frames); "near perfect" (Fstoppers). 4 sources.
3. **The face close-up panel**: every detected face at once, no zooming (Narrative, Aftershoot's Key Faces, Lightroom's Face View). "This function alone makes it worth it" for groups. 4 sources.
4. **Rejects kept, not deleted**, and a human pass over them: Aftershoot's buckets, Immich's review-then-approve, OptiCull "leaves final judgment to the photographer". 4 sources.
5. **Support quality drives loyalty**: named support staff in Trustpilot reviews for Aftershoot and FilterPixel, dozens of times. 2 vendors, many reviewers.
6. **Local processing valued** for client RAWs: Excire, Optyx, OptiCull, Aftershoot desktop, Immich. 5 sources.
7. **Flat pricing over per-image.** 3 sources.

## How professionals cull without AI

- **Two methods, both one key per frame.** Reject-first in three passes (backwards flagging failures, forwards flagging keepers, then bursts and flow), 3,000–5,000 RAW in 60–90 minutes, 1–1.5 s per frame (comfypixel). Or pick-first in one pass: "you should be selecting images to keep, not selecting images to reject" (Shotkit). Both say: skip stars; one flag and one reject cover 95% of weddings.
- **Bursts by hand**: one frame per burst on expression or gesture; a second only for "a genuinely different beat"; a cap of three delivered per burst; Lightroom's Survey (N) for the burst, Compare (C) for the pair.
- **Photo Mechanic's whole advantage is the embedded JPEG**: no import, no render; "what takes 2–3 hours in Lightroom can be done in 30–45 minutes" (Imagen's comparison ~). Its weakness is the same fact: the embedded JPEG is not the frame, so critical focus is judged elsewhere.
- **Client proofing is one-way**: Pixieset favourites, ShootProof favourites and rejects, Pic-Time selling more than proofing. "Neither is built for collaborative feedback." Picks come back as a list of names.

## The market, and the mood

| Number | Value | Source |
|---|---|---|
| Wedding photography, global | $26.1B (2024), 8% a year | market.us |
| Wedding photographers | ~450,000 worldwide; ~120,000 active in the US | zipdo; jacobrussellphotography ~ |
| Aftershoot | $16.1M raised; ~$3.5M revenue and ~$52M valuation (2024, third-party estimates); 188k photographers (2025); 8.8B images processed, 1.24B of them duplicates, "one in six frames" | Tracxn, PitchBook, aftershoot.com (vendor) ~ |
| Photographers using AI in the workflow | 83%; 68% of working pros weekly or daily (mostly admin, captions, marketing) | Zenfolio 2026, 4,900 respondents |
| Time on editing | ~70% of pros spend 26–75% of their working time | Zenfolio 2026 |
| A full-day wedding | 1,500–3,000 shot, 400–800 delivered | shootandthrive, selekt |
| Human culling services | $0.06–0.30 per image, or $119–299 a month | ShootDotEdit, Evolve, Imagen's cost guide ~ |

The mood, in their words: "I haven't yet met any photographer who enjoys
the culling process" (Fstoppers). Every vendor now markets "learns your
style, doesn't replace it" and "confirm, not obey" (Aftershoot, Narrative,
FilterPixel, Imagen), which is marketing following a demand. The worries
are deskilling ("the loss of skills is something to be mourned"), the
subjectivity of "best", client RAWs in the cloud, and the "AI slop"
reaction toward imperfection as authenticity. The hobbyist mirror is the
same trade: "sometimes we'd rather have the funny photo with closed eyes
than the picture-perfect smiles." Aftershoot's March 2026 pledge (ask
permission, never replace, build with photographers) was a response to a
competitor's generative tools; the market is rewarding the posture this
app already holds.

## What photographers say, in their own words

296 comments from the archive, read after the rest of this document was
written, which makes them a test of it rather than more evidence for it.

**Read this first.** One hostile account wrote 21 of the 118 comments
mentioning Aftershoot, repeating the same accusations. The volume of
anti-Aftershoot feeling here is partly one prolific voice, not 21
independent ones, and is discounted accordingly.

**The contradiction that matters.** This document says the universal
complaint about AI cullers is the false reject of a keeper: shallow depth
of field, deliberate blur, a deliberately closed eye. In 286 comments of
photographers talking to each other, *that specific complaint does not
appear at all*. The complaint that does appear runs the other way: the
tools do not reject enough. "It couldn't even consistently get rid of the
pictures of the floor" (r/Lightroom, 2026-02). "The culling is basically a
random selection, it's completely useless" (r/photography, 2026-09). The
general worry behind the false-reject framing is real and stated
("no ai ever will know what was my intention during a scene and if I
wanted to keep it or not", r/WeddingPhotography, 2026-06), but the
specific trigger the review sites report is a review-site artefact. The
working pain is under-rejection, not over-rejection.

**The redundancy-versus-judgment split survives, with a better number.**
This document cited one reviewer's "5 to 10 percent". A photographer's own
account is sharper and larger: "I can use Aftershoot to cull away the real
duds and reduce the image count by ~30%. That's pretty reliable ... But to
get it down to an actual 'final cull' has been extremely unreliable"
(r/WeddingPhotography, 2026-06). Others report nothing at all: "cancelled
as it literally saved me no time with 50k images" (2026-05); "every time
it's added to my workload not reduced it" (2026-05). Reliable on duds,
unreliable on judgment, which is the split this design is built on.

**Everyone reviews the rejects, confirmed outright.** "AfterShoot is AI
based but it just puts info in a sidecar so I review its decisions
afterwards" (r/Lightroom, 2026-01). "Let it do the first run through for
me, then I go in and do a final review" (r/WeddingPhotography, 2026-05).
No comment in 286 describes trusting a blind export.

**Speed, confirmed.** Photo Mechanic across 52 mentions, praised for
almost nothing but speed: "runs at speed of thought" (r/Lightroom,
2026-05); "Never ever mass ingest images directly into Lightroom unless
you want your computer to grind to a halt" (r/sportsphotography, 2026-09).
The speed *complaint* lands on Lightroom's preview building, not on the AI
tools: "waiting on preview builds before I can even start culling is
maddening" (r/Lightroom, 2026-07).

**Stars are not dead, and they are doing a job.** This document said
working photographers prefer one flag and one reject. Both schemes are in
use with no winner, and the multi-pass star workflow is valued precisely
because it carries a "maybe" between passes: one star on the first pass,
four or five on the second. That is a real requirement and this design
meets it a different way, so `docs/SELECT.md` now says which.

**"No tool learns which frame you keep" is disputed.** One long-time user:
"it also learns. The more you swap around ratings in the app, your cull
will get better over time" (r/WeddingPhotography, 2026-05). Others report
the opposite over months: "25k training images and it just never got
better" (r/Lightroom, 2026-05). One comment against several, so the seat
is not as empty as this document claimed; it is contested.

**Local processing is a live grievance, not a preference.** A poster
quoted Aftershoot's own terms — "Use your photographs and editing data to
train artificial intelligence systems that power the Aftershoot
application" — against its local-processing marketing
(r/WeddingPhotography, 2026-04). Whatever the merits, a product that
genuinely never sends a frame anywhere has something to say here.

**Times and passes, in their numbers.** Two to four passes is the norm.
"Culling is about a couple of hours, edit between 6 and 15 hours" per
wedding (2026-08). "Culling takes me 2-4h if I'm concentrating ... I never
use the newer tools, they cost me more time than they save" (2026-08).
Sports: 4,000 to 8,000 frames an event, culled in camera between plays.

**What they ask for that nothing does.** "I wish the auto button was
actually a predictive editing button ... based on my patterns and past
behaviours" (r/Lightroom, 2026-08). And a founder building a rival names
the same gap this design names: "a fast, keyboard-driven photo review
workspace for photographers who want to move through large shoots without
handing the final decisions to a black-box auto-cull"
(r/WeddingPhotography, 2026-08).

## What it decides

Ten things the evidence settles for `docs/SELECT.md`, most of them
confirming the draft, two of them changing it, and one of them standing
only after the photographers' own words took its stated reason away:

1. **Never mark a frame bad.** Not for the reason the review sites give.
   The false reject of a keeper does not appear in 286 photographers
   talking to each other; the working complaint is the opposite, that the
   tools do not reject enough. The rule stands on the other ground: an
   absolute verdict is a claim about the photograph that only its owner
   can make, and a tool that makes it is not trusted with the next one.
   Relative within the scene ("there is a better one here") is a claim
   about redundancy, which is the claim the archive can actually support.
   Confirms, on a different footing; and under-rejection is an argument
   for the cull, not against it.
2. **Sell the redundancy pass, not the judgment pass.** The saving vendors
   claim is hours; the saving a working reviewer measured is 5–10%,
   because emotion is still his. Our split, in the market's words.
   Confirms; sharpens the copy.
3. **The second look is how the tools are actually used.** Everyone
   reviews the reject buckets. Confirms; the second look is not optional
   and should be one screen, fast.
4. **Speed of the pass is the first praise and the first complaint.**
   Instant switching, no render, no import wait. The pass must be as fast
   as Photo Mechanic and as sure of focus as a real decode, which the
   tile-and-loupe scheme already gives. Confirms.
5. **One flag and one reject, no stars — but the "maybe" is real.** Both
   hand methods teach one flag and one reject, and the pass's keys stay
   keep, swap, nothing, compare with no rating anywhere. What the star
   workflow is actually for is carrying a maybe between passes: one star
   on the first pass, four on the second. The cut already carries it —
   a frame in the select after the first cut is the maybe, and the next
   cut is the second pass — so the design owes the reader that sentence
   rather than a rating. Confirms; **sharpens the design**.
6. **Faces at once.** The face close-up panel is the one AI-era surface
   every reviewer praises. **Changes the design**: the pass's strip gains a
   face row when the scene has faces, every detected face at once with its
   eyes and sharpness read (the faces pass and the sharpness pass already
   produce them), so a group scene is judged without zooming.
7. **The log is the only state, and marks never vanish.** Lightroom's
   silently reverting stars are a top-three complaint. Confirms; and worth
   a proof that a select survives a close, a crash and a reindex.
8. **Local, always.** Not a preference but a live grievance: a
   competitor's own terms reserve the right to "use your photographs and
   editing data to train artificial intelligence systems", read against
   its local-processing marketing. A product that genuinely never sends a
   frame anywhere has something to say, and can only say it if it is
   true. Confirms the product boundary, and raises what it is worth.
9. **Learning which frame a person keeps is contested, not empty.** No
   tool in the table documents it, two learn an editing style, and one
   long-time user reports that swapping ratings does improve the cull
   while others report months with no change. Either way nobody can point
   at what was learned. Confirms step six, and shifts what it has to earn:
   not the first to learn, the first whose learning is legible.
10. **Client proofing is one-way everywhere.** **Changes the design**: the
    contact sheet's numbers coming back as decisions with the client as
    author is the practice's missing half, and the log already allows it;
    it moves from the rejections' "second curator" (a co-editor in the
    app, still rejected) to a plain import of marks.

And one **change of emphasis**: coverage against a shot list (the list) is
the delivery feature the market has not shipped; Apex Culler claims "shot
list detection" but has no independent review. It stays, as search reused.

## Sources

Aftershoot: aftershoot.com (pricing, culling FAQ, keyboard shortcuts, Spray
Can, AI profile, Snapshot 2025, the March 2026 pledge); PetaPixel 2026-03-25;
Fstoppers reviews (Rackham, Jon The Baptist, Palmer); Shotkit review;
Trustpilot (au) 1,766 reviews incl. Léo, Aug 2026; Digital Camera World on
the 89M-hours claim; Tracxn and PitchBook for funding (third-party).
Narrative: narrative.so/pricing; Fstoppers "fastest way to cull"; Shotkit
review. Imagen: imagen-ai.com; PetaPixel 2025-04-08 and 2026-05-26;
FilterPixel's pricing and comparison pages (vendor). FilterPixel:
filterpixel.com; PetaPixel 2022-05-18; Trustpilot (ca) 174 reviews. Optyx:
Shotkit; The Phoblographer; Fstoppers "spend less time culling". OptiCull:
PetaPixel 2023-06-07. Lightroom Assisted Culling: PetaPixel 2025-11-03 (two
pieces); DPReview June 2026; Adobe Community early-access thread. Photo
Mechanic and hand culling: Fstoppers (Mike Smith and comments); comfypixel
three-pass workflow; Shotkit culling guide; Capture One support and
alexonraw; Imagen's Photo Mechanic comparison (vendor); Adobe Community and
Lightroom Queen on vanishing ratings and Bridge. Proofing: picdrop
comparison; shootproof.com. Organisers and platforms: Digital Camera World
(Excire); Excire's own comparison (vendor); PetaPixel and Fstoppers
(Peakto); mylio.com; PhotoPrism docs and GitHub #1590; Immich docs and
AlternativeTo; XDA; MacRumors and Apple Community on duplicate merge;
Google Research "Top Shot on Pixel 3"; Android Police; TechRadar on Best
Take; TechAdvisor on Highlights; 9to5Google on Moments; Tom's Guide and
Samsung on Best Face; Microsoft Store listings. Market and mood: market.us;
zipdo; Zenfolio 2026 State of the Photography Industry; Fstoppers "should
we be cautious"; Adobe Community AI-culling feature request; Creative Bloq
and Big Issue on the AI reaction; ShootDotEdit and Evolve pricing; Chatbooks
and Mixbook reviews (Tom's Guide, TopConsumerReviews). Research:
arXiv 2009.03224 and 1104.4723 on near-duplicate detection; "Automatic
triage for a photo series" (abstract only ~).
