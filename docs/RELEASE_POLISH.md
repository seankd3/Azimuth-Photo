# Azimuth Photo — Release-Readiness Program

The app works end to end. This program is everything between "works" and
"a stranger downloads it, loves it, and trusts it with their life's photos."
Frozen 2026-07-13. Waves roll until the box is empty.

Doctrine: Fable/Opus owns visual taste, copy, and delight (empty states,
onboarding, screenshots, motion). Codex owns mechanical/systems/testable work
(pipelines, validation, plumbing, docs-with-structure). Every lane proves
itself; cross-model review before merge; never trust narration.

## Wave 1 — the three things that gate a release (Codex, in flight)

### R1 · Release & auto-update pipeline  (systems)
The single biggest gap: you cannot ship an app people can't update.
- Tag-driven GitHub Actions release: build + publish the Docker image to ghcr,
  attach the frozen server binary and (stub) the signed desktop installer to a
  GitHub Release.
- Tauri updater wired to the GitHub Releases manifest (signing keypair as a
  documented setup step, not committed).
- `/api/version` endpoint; hub↔satellite version handshake so a stale server
  raises a friendly "your server needs an update" banner instead of cryptic
  sync errors. Semver compare, migration-safe.
- `docs/RELEASING.md`: cut-a-release runbook.

### R2 · Security, trust & legal for release  (systems)
Can't open-source or hand to a stranger without this.
- LICENSE (confirm with Sean which), THIRD_PARTY_NOTICES, film-stock provenance
  table, SECURITY.md + a short threat model.
- Finalize the device-token auth path (opt-in, but correct and enforced when on).
- Share-link hardening: rate-limit unlock attempts, constant-time password
  compare, no token/hash leakage in errors or logs.
- Input validation + security headers pass on all public routes; verify no raw
  tracebacks or internal paths ever reach a client.

### R3 · Resilience & error surfaces  (systems + Fable-specified copy)
The difference between "demo" and "product" is how it behaves when things break.
- Structured error envelope on every API path; a client-side toast/error bus;
  friendly retriable messages (copy provided in the brief) — never a raw 500.
- Graceful degradation everywhere a worker/dep/drive can be missing (already
  partly true — make it total and tested).
- 404 / offline / "server unreachable" states that explain and offer the fix.

## Wave 2 — performance & the loved details (mixed)
- Rest of #35: progressive base loading + speculative pregen (Develop territory).
- Empty states, loading skeletons, first-run delight — **Fable/Opus owns**.
- Accessibility release audit (focus order, ARIA, contrast, reduced-motion).
- Onboarding: optional 60-second tour, sample library, contextual help.

## Wave 3 — the front door (mixed)
- README with real screenshots + a 30-second value pitch.
- Landing page refresh (azimuthphoto.com — deploy stays gated on Sean).
- Mobile PWA polish: install prompt, offline shell, app-icon set.
- A "what's new" / changelog surface fed by releases.

## Standing rules
- Data-loss-adjacent paths (trash, publish, delete) get extra test love.
- Every public-facing string is Sean's brand voice: calm, confident, plain.
- No feature ships without an empty state, a loading state, and an error state.
