# Grid placeholder cards — spec (v1, frozen 2026-07-16)

## Why

The grid currently hides every photo until its `sm` preview exists. Tonight's fixes
made that honest (preparing state, live-growth poll) but the underlying design still
means: a worker failure looks like an empty library, counts disagree with the canvas,
and photos "pop" into existence instead of the library feeling instantly present.

Product bar: **a photo you imported exists on screen immediately** — as a card that
sharpens, never as a void that fills.

## Behavior

1. **Rankings stops hiding pending photos.** `hidden_pending_thumbnails` becomes
   `pending_thumbnails` metadata; rows return regardless of preview state, each row
   carrying `preview_ready: bool` (and `aspect_ratio` when known — fall back 3:2).
2. **Grid renders placeholder cards** for `!preview_ready` rows: correct-aspect box,
   `--surface-2` fill, subtle 1.2s shimmer, filename bottom-left in `--fs-micro`
   muted. No spinner per card. Selection/flagging/keyboard work on placeholders
   (they are real photos).
3. **Swap-in without layout shift:** when the `sm` preview lands (existing poll,
   currently gated to top-of-scroll — keep gating for reflow but allow in-place
   `src` swaps anywhere: a placeholder that learns its thumb URL just sets `src`,
   no relayout since the box already has final aspect dimensions).
4. **Empty states simplify:** "Preparing your photos" survives only for the
   zero-rows instant (first seconds of a first import before scan registers rows).
   The count in the header equals visible cards, always.
5. **Loupe/develop on a placeholder:** allowed; they already have their own
   progressive loading (proxy → full). Never block navigation on preview state.
6. **Mobile timeline:** same rules, same swap-in; month covers may use a
   placeholder only if no member of the month has a ready preview.

## Non-goals (v1)

- No skeleton animation library, no new dependencies.
- No changes to preview generation priority (pregen fairness already landed).
- Virtualization math: placeholders use known aspect ratios, so `grid_window.js`
  estimates get *more* accurate, not less — but do not rewrite estimation.

## Acceptance

- Fresh import of N photos: N cards visible within one poll cycle of scan
  registration, zero layout shift as previews land (measure: card positions before
  and after swap are identical).
- Kill the preview worker mid-import: cards remain, shimmer persists, count
  honest, no empty state — the failure is visible as "cards not sharpening",
  which is the truth.
- All existing preparing-state/live-growth contract tests updated to the new
  model in the same commit that changes behavior (no red gates between commits).
