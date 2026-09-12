# Panorama detection and stitching — the research brief (2026-09-12)

Made for Sean's ask of 09-12 ("automatically detect and merge shots that are
shot as a panorama… it would have to be done extremely well"). The ledger rows
it produces are PN1–PN3 in `FINDINGS.md`.

**In one sentence.** Detection is already 80% built — `stacks.py` finds beats;
a panorama is a beat plus a consistent one-way overlap measured on the 1,024 px
tiles — and stitching quality is decided by four things: bundle adjustment over
a rotation-only camera model, projection chosen by field of view, exposure
handling before the seams, and honest refusal. Not by the feature detector.

## Ranked design decisions

1. **Propose, never auto-group.** `stacks.py`'s own law: nothing is stacked for
   you; the machine keeps the proposal. Lightroom, Capture One, PTGui, Hugin
   detect nothing (merge is invoked on a selection); Apple Photos' "Panoramas"
   album is an aspect-ratio filter over already-stitched images. Automatic
   detection is genuinely new surface, which is why false positives are the
   whole risk: ship it as a badge and a proposal on a run.
2. **Geometry verification kills the false positives, not metadata.** Brown &
   Lowe's probabilistic verification: a pair overlaps when the RANSAC inliers
   exceed `5.9 + 0.22 × features in the overlap` — the same rule OpenCV's
   stitcher confidence implements.
3. **Rotation-only camera model with bundle adjustment, never chained pairwise
   homographies.** Chained homographies drift and cannot close a sweep; BA over
   (rotation, focal) with a ray cost is what separates PTGui/Hugin from
   "warpPerspective and hope". PTGui/Hugin also win on lens models and masks.
4. **Projection by measured field of view.** Under 60° rectilinear, 60–120°
   cylindrical, over 120° or any vertical sweep spherical. Auto-select from the
   summed horizontal field BA reports; let the owner override.
5. **Refuse loudly.** Parallax, movers and textureless sky are the failures,
   all detectable before the full-resolution composite. "This will not stitch:
   3.4 px median residual, likely parallax at the left edge" beats a silently
   ghosted 200 MP file.

## The detection rule to ship

Run on any cadence run of three or more frames, or any three or more
consecutive frames within 5 s of each other.

- **Gate A — metadata (SQLite only).** One camera, lens, focal length,
  orientation and frame size; exposure, aperture and ISO identical or at least
  not a symmetric ±n bracket; white balance identical when recorded; median
  gap ≤ 8 s.
- **Gate B — geometry, on the 1,024 px tiles (~40 ms a pair).** Per
  consecutive pair: ORB (2,000 features), Hamming matcher, ratio test, RANSAC
  homography. Overlap fraction (warped corner polygon) in [0.15, 0.65]; the
  inlier rule above; translation direction consistent (circular std under 20°,
  no sign reversal — a monotone sweep, which rejects re-composed and "one more
  time" repeats); step-size coefficient of variation under 0.5; frame i→i+2
  must overlap less than i→i+1 (a burst overlaps ~100% everywhere); reject if
  the median pair overlap is over 0.85 (a burst, not a sweep).
- **Gate C — SigLIP-2 as a prior only.** Neighbour cosine high and strictly
  falling with distance is sweep-shaped; flat-high is a burst. Ranks
  proposals, never accepts one.

Ship A ∧ B. The i→i+2 test and the monotone direction kill bursts and
brackets; Gate A kills HDR sets.

## The stitching pipeline

Use the detailed API (`cv2.detail`) once the confidence report is wanted;
`cv2.Stitcher` is the right shortcut for the second slice. Its defaults:
registration at 0.6 MP, seams at 0.1 MP, compositing at full size; SIFT;
best-of-two-nearest matcher (confidence 0.3); ray-cost bundle adjustment;
horizontal wave correction; blocks-gain exposure compensation; graph-cut
seams on colour; multi-band blending (strength 5).

Knobs that matter, in order: the warper (override spherical by the field-of-view
rule); registration resolution (raise to 1–1.5 MP for skies and low texture);
exposure compensation (blocks gain with two or three feed passes beats plain
gain on sun-side sweeps); seam scale; the confidence threshold. Known: the
stock pipeline does not compensate exposure before seam finding, which puts
seams wrong on graded skies (opencv#7459) — compensate, then find seams, in
our own loop.

Steps: undistort by the lens profile → SIFT/AKAZE at 0.6–1.2 MP (AKAZE when
SIFT starves on sky; ORB only for the preview) → pairwise match with the
verification rule → focal estimate from the homographies (median) → bundle
adjustment (ray) over rotation and focal → wave correction → projection
select → blocks-gain compensation → graph-cut seams at 0.1 MP → multi-band
blend (bands ≈ log2(min dimension) − 3) → auto-crop to the largest inscribed
rectangle, keeping the uncropped canvas.

**Failure detection, emitted, never hidden:** BA median reprojection residual
(over 2 px at 0.6 MP warns, over 4 px refuses); any frame dropped by the
confidence threshold; overlap-region SSIM after warp (low, with the residual
localised in one band, means parallax or movers); an estimated focal
disagreeing with EXIF by over 15% means wrong lens data.

## The raw path

Stitch linear, demosaiced, 16-bit, before the tone curve, in the working
colour space: the professional consensus and PTGui's own model. Blending in
linear light is physically right (no seam banding from gamma-encoded data),
highlight headroom survives, and the merge stays editable. Lightroom writes a
raw `-Pano.dng`, so every raw edit remains available, plus Boundary Warp
(mesh-warp out to the rectangle instead of cropping) and Fill Edges.

Practical: write a linear DNG (demosaiced, LinearRaw, ColorMatrix and
AsShotNeutral from the first frame), else a 16-bit ProPhoto TIFF. Metadata:
capture time of the first frame; lens, focal, ISO, exposure from it; GPS the
mean of the members; the members named in the sidecar. The result enters the
catalog as a normal photograph and becomes the stack's cover.

## Performance on this machine

Six 24 MP frames: the preview from 1,024 tiles in 0.6–1.5 s (ORB, BA, feather
blend). The full composite is warp plus multi-band blend over a ~150 MP canvas:
45–120 s and 3–6 GB peak; the raw path adds 2–4 s a frame for demosaic.
Precompute at detect time and cache on the run: features, pairwise
homographies, focals from the 1,024 tiles — they scale exactly, so the
full-resolution merge reuses the BA solution and re-runs only seams (at 4,096)
and blending. CUDA is not worth it: OpenCV's CUDA path covers only warping and
blending, 4 GB cannot hold a 150 MP pyramid, and the build is fragile. Tile
the blender instead.

## Build order

1. **Detect and preview.** Gates A and B over runs, cached pair geometry, a
   badge on the run, a preview strip from the 1,024 tiles, the confidence
   report and the honest refusal. No merged file is written. This is
   `stacks.py`'s existing proposal with one new fact (pair overlap).
   *Shipped 2026-09-12 as PN1 (the fact: `panorama.of`, the inspector row,
   S stacks the sweep) and PN4 (the preview: `panorama.preview` merges the
   loupe tiles with OpenCV's stitcher, trims the canvas, keeps one file on
   the run; the Panorama fact opens it in the loupe as a preview). The
   preview is judged from the 4,096 px loupe tiles rather than the 1,024,
   because they were there and cost the same seconds.*
2. **Full-resolution merge.** The detailed API reusing the first slice's BA;
   projection by field of view; blocks gain, graph-cut seams, multi-band;
   auto-crop with the canvas kept; a 16-bit TIFF that becomes the stack cover.
3. **The raw path.** rawpy linear demosaic → stitch in linear → linear DNG
   with full metadata and sidecar; then boundary-warp edge recovery as one
   slider. Only after the second slice proves the geometry.

Sources: Brown & Lowe 2007 (IJCV); OpenCV `stitching_detailed` and the
stitching module docs; opencv#7459; Adobe's panorama merge and Boundary Warp
pages; PTGui FAQ; panotools 16-bit workflow; Hugin's optimisation manual.
