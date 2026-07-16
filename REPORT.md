# ux4-sharing report

Branch: `ux4-sharing` (not merged to `develop`).

## Status by work-order item

1. Done — Client galleries now render `share_gallery.html`; the inline client page is removed. Locked galleries use the shared lock card and rate-limit copy.
2. Done — Open private shares, client galleries, and static published galleries emit an `og:image`; locked shares do not.
3. Done — Private-share download copy says “web-size copy”; published gallery downloads use the generated `lg` JPEG.
4. Done — Private-share Download all navigates to one ZIP response at `/s/{token}/download-all`.
5. Done — Lightbox opens its `md` preview then upgrades to `lg`; it supports pinch/pan zoom and double-tap toggle. Swipe navigation only acts at 1x.
6. Done — A client gallery’s chosen cover is rendered as a full-width visitor hero with the gallery title and brand.
7. Done — Static site nodes render parent back links and child-gallery cards with a cover/first-photo thumb and count.
8. Done — Selecting Done persists `client_finished_at` on the share, returns it to the owner favorites payload, and changes the visitor confirmation to “Sent”.

## Manual QA

Not run. Click through: open an unprotected `/s/` link and a `/s/gallery/` link; verify branding, lightbox arrows/Escape/swipe, the `md`-then-`lg` image upgrade, pinch zoom, individual web-size downloads, and one ZIP download. Lock each link, submit wrong passwords until throttled, and confirm the shared lock message. Set a gallery cover and verify the hero. In a nested static export, follow child cards and the back link. Pick favorites, press Done, then inspect the owner favorites response for `client_finished_at`.

## Verification

- `~/Projects/photo-archive/web/.venv/bin/python -m pytest test_*.py -q -k "share or publish or gallery"` from `web/` — `84 passed, 924 deselected`.
- `node --check static/js/desktop/gallery_editor.js` — passed.
- `git diff --check` — passed.
