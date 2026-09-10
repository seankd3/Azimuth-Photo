# Taste, sharpness and culling — the research brief (2026-09-10)

Three read-only studies made for Sean's asks on 09-10 ("make the taste algo
smarter", "study Aftershoot and Lightroom's assisted culling", "subject
sharpness detection and more"). This is the distilled plan; the ledger rows
it produced are in `FINDINGS.md` and the experiments are measured on Sean's
own rounds (held-out duel accuracy and top-decile recall, as
`scripts/sim_learn.py` measures), never on a public benchmark.

## What holds, from the literature and our own numbers

- **The head is linear and stays linear.** LAION's aesthetic "MLP" is a
  linear map; a 64-unit and a 256×64 head both lost to ridge on 16k of our
  photographs; personalised aesthetics papers (PRAC, PARA, FLICKR-AES)
  gain 2–4 points over a generic model and mostly re-scale rather than
  re-order. No encoder that fits the 4 GB card beats SigLIP-2 on Sean's
  duels (Qwen3-VL 8B tied).
- **The measured gains come from three levers**, in Kong et al.'s order:
  fitting the rounds directly with a pairwise/listwise loss (+.03),
  appending attribute facts a fit can weight (+.014), and a within-genre
  residual (+.008). Frozen-feature Bradley–Terry with fifty pairs beat a
  fully fine-tuned model in the one domain it was tested.
- **Facets are facts, never a blended scalar.** Adobe's own culling
  principles say the same: each signal evaluated independently, technical
  and subjective split, assist don't decide, when in doubt keep it. The
  August "quality" scalar was deleted for being hand-weighted; Top Shot's
  combiner is a *fitted* additive model, which is the whole difference.
- **Sharpness is relative.** Every classical measure confounds low texture
  with defocus and most reward noise; Zhu–Milanfar's gradient-SVD measure
  is the one that penalises noise. Score the subject against the frame as
  a ratio, on the right rendition (eyes from the 4,096 px loupe, the frame
  map from the 1,024 px tile), and store the numbers, not a verdict.
- **The culling tools' users fail on trust, not on signals**: keepers
  rejected for shallow depth of field, blinks in background faces counted
  against the frame, opaque scores, undo that forgets the person's own
  decisions, marks that vanish. Relative-within-stack ("there is a better
  one in this stack") is defensible; an absolute "bad" is not.

## The facets (each a stored fact, per face or per frame)

| Facet | Method | Cost |
|---|---|---|
| Eyes open | EAR from the 2d106 eye contours (indices 33–42, 87–96) plus OCEC (112 KB ONNX, 0.16 ms); disagreement → "can't tell" | negligible on the face pass |
| Eye sharpness | Zhu–Milanfar H on each eye crop from the 4,096 rendition; face_px stored as the reliability gate | ms |
| Face sharpness | the same on the face box | ms |
| Frame map | MLV or S3 on 32 px blocks of the 1,024 tile → p50, p90 | ms |
| Subject sharpness | p75 inside the subject box (nearest face, else u2netp ~30 ms only for no-face frames) ÷ frame p75 | ms |
| Blur type | background gradient anisotropy, one number; never a classifier | free |
| Expression | EmotiEffLib enet_b0 (16 MB) valence/arousal + 8 probabilities per face | one overnight CPU lane |
| Colour harmony | Cohen-Or template fit over the hue histogram already in photostats | ms |
| Exposure | already in photostats | done |

Shown on the tile only where calibrated (a blink as a small mark; a face box
coloured by sharpness in the loupe), and weighted only by the fit below.

## The head

One Plackett–Luce fit on the rounds: `score = v·w + f·u`, ridge on `w`,
a separate penalty on the standardised facet block `u`, a per-cluster
residual `δ_k` penalised toward zero, and Bayesian-ridge variance as the
uncertainty Learn draws with. Burst-best is the same fit restricted to
frames of one stack, and the "why" on a tile is the largest term of
`w ⊙ (x_best − x_this)`.

## The experiments, in order

1. **E1** One-stage PL fit on the rounds vs the two-stage strength→ridge. Data on hand; minutes of CPU; expected +1–3 duel points and one seam deleted.
2. **E2** The eye and sharpness facets as a `sharpness` cache kind (faces first, newest first), then the facet block in the fit. An overnight lane; the largest gain on portrait rounds; reported per genre.
3. **E3** The per-cluster residual. Minutes; within-genre is where the room is (+.35 today).
4. **E4** Bayesian-ridge variance as Learn's σ, and top-two finding rounds. Minutes; 2–3× sample efficiency in the bandit literature.
5. **E5** Burst-best within cadence stacks: facets alone against Sean's picks. Tells whether E2 earned its tile marks.
6. **E6** Expression, then composition (SAMP-Net) only if E2 shows the eyes matter.

Not done, and why: no new encoder (measured tie, nothing else fits); no MLP
or neural head (measured loss); no GP or kernel for accuracy (gains only at
one to sixteen shots); no NIMA/MUSIQ/LAION scores as features (generic
consensus, a weak derivative); no absolute thresholds anywhere; no per-photo
segmentation model heavier than u2netp (seconds each on this CPU).

## Sources kept

Rauno Freiberg's interaction essays; Emil Kowalski's motion standards; Adobe
Design "Behind the design: Assisted Culling"; Pertuz et al. 2013 (focus
operators); Zhu & Milanfar 2009/2010; Soukupová & Čech 2016 (EAR); PINTO0309
OCEC; Chang et al. 2016 (photo triage); Google Research "Top Shot"; Kong et
al. 2016 (AADB); Schuhmann's aesthetic predictor; the PRAC and PARA papers;
Maystre & Grossglauser 2015 (Plackett–Luce inference, `choix`).
