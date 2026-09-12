# DELIGHT_SPEC — Wave 2: the polish that makes it loved

Everything that separates "a capable tool" from "an app people are delighted
by." Fires when Wave-1 lanes free up. Fable/Opus owns the taste items; the two
systems items can go to Codex. Explicitly does NOT touch error toasts, the 404
page, or the connection banner — the RESILIENCE lane owns those; coordinate at
merge. Frozen 2026-07-13.

## D1 · Empty states, everywhere (Fable owns)
Every list/grid/panel that can be empty gets an intentional state: an icon or
faint illustration, one calm sentence, and the single action that fills it.
No blank rectangles, ever. Inventory + exact copy:
- **Library, no photos** (post-setup, source removed): "No photos here yet." +
  "Add a folder to start your library." → button opens the add-folder flow.
- **Search, zero results**: "Nothing matches *«query»*." + a why line if the
  scope is the reason ("Try clearing the People filter") — reuse the existing
  zero-scope reason logic. Never a bare empty grid.
- **Collection, empty**: "This collection is empty." + "Drag photos here, or add
  from the grid."
- **Folder with 0 kept**: "Every photo here is culled." + link to show rejects.
- **Trash, empty**: "Nothing in the trash." (quietly reassuring, no action.)
- **People / Map / Timeline with no data**: each states why (not scanned yet /
  no GPS / no dates) and the enabling action.
Design: centered, --text-3, generous vertical space, matches the wizard's quiet
tone. One shared `.empty-state` component; do not one-off each.

## D2 · Loading, never a flash of nothing (Fable owns)
- Skeleton cells in the grid while the first page resolves (the virtualized grid
  already knows cell size — render shimmer placeholders at the true aspect until
  the thumb lands). Reuse the develop progressive-paint timing vars.
- Panels (Info/Metadata/Keywords/People) show a subtle skeleton, not a jump.
- Any action >400ms shows progress (import already does; audit the rest:
  export, publish, rebuild stacks, model download).
- Respect `prefers-reduced-motion`: shimmer → static dim.

## D3 · First-run delight & onboarding (Fable owns)
Beyond the setup wizard: the first time the library opens, a *dismissible*,
non-blocking welcome that teaches the three superpowers in one breath —
"Press **Space** to cull · Type to **search by meaning** · **Compare** to rank
your best." Coach marks anchored to real UI, skippable, never shown again.
Plus: seed a graceful "your library is still building thumbnails" hint that
fades as pregen catches up.

## D4 · Accessibility release audit (Codex-able, then Fable verifies)
Full pass: logical focus order, visible focus rings that match the design,
ARIA roles/labels on every custom control (the grid, sliders, the compare
picker, drawer tabs), color-contrast check on --text-2/--text-3 against every
surface, `prefers-reduced-motion` honored globally, keyboard reachability of
every action (the shortcut sheet is the spec of intent — verify reality matches
it). Produce an AUDIT with findings + fixes; land the fixes.

## D5 · Progressive base loading + speculative pregen (Codex — Develop systems)
The remainder of task #35. Develop already progressive-paints; extend the same
instinct library-wide: predictively pregenerate the thumbs/bases the user is
about to need (next grid page on scroll velocity, loupe neighbors ±N, the
picked set, develop siblings) so browsing feels precognitive. Budget-bounded,
never competes with a user-triggered decode. Measure and prove the perceived
win against the new perf budgets.

## Bar
Notion-level. Sized-to-content, keyboard-first, no hover-gating, no dead ends.
If a state can exist, it was designed on purpose.
