# photoArchive — agent brief

Local-first photo library. Product bars: **mobile = Google Photos replacement,
desktop = Lightroom Classic replacement (plus publishing)**. Ranking (Elo from
effortless picks) is the moat; local privacy is the promise. Open source, free.

## Read first
- `docs/ui-architecture.md` — THE design charter. Every UI change must pass it
  (grammar: nouns/verbs, three-layer law, "no new pages", disclosure ladder,
  speed covenant). Do not improvise UI structure.
- `docs/development.md` — app shape, Where-To-Edit table, verification ladder
  (`./scripts/photoarchive-check --area <x>`), development discipline.
- `docs/product-roadmap.md` — build order (collections → sharing → publishing
  → mobile → platforms → AI assist).

## Current state (2026-07-06)
- UI direction is settled: the "photoArchive One" synthesis. Reference
  implementation lives UNTRACKED at `web/static/prototypes/one.html`
  (plus five paradigm prototypes + index). Prototypes are read-only against
  real APIs; all writes simulated — keep it that way in prototypes.
- Production UI port is beginning: mobile shell first (real writes + PWA),
  desktop lenses after. Check recent commits before assuming state.
- Backend recently hardened: multi-word hybrid search, sort_quality in
  rankings responses, purge orphans fixed, relative Elo propagation, event
  loop protected from slow disks. Suite: 301 tests, keep it green.

## Rules of the road
- Tests/checks run on omarchy with the repo venv: `./scripts/photoarchive-check`
  (use the smallest `--area` that covers your change).
- Server control: `~/Projects/mobile-app-toolkit/bin/mobile-app photo-archive
  server restart` (binds Tailscale). The repo script binds localhost only —
  do not use it for phone-facing runs. Never use `localhost` URLs for phone
  work; use `http://omarchy.tail0eeded.ts.net:8000`.
- Android build/install/e2e: `mobile-app photo-archive android <build|install|e2e>`.
- Runtime data (web/photoarchive.db, .thumbcache/, .models/, .run/) and
  process files (AGENTS.md, CLAUDE.md, TODO.md) are never committed.
- API discipline: public routes/shapes are contract-tested
  (web/test_modular_contracts.py) — new routes must be registered there.
  Mutating routes use typed Pydantic bodies. Never leave sync disk/CPU work
  on the event loop (asyncio.to_thread).
- The real archive is ~47k photos / 2GB SQLite / 2.35M ranking signals on
  this box. One catalog source may legitimately be offline (unplugged
  drive) — treat that as a normal state, not an error.
