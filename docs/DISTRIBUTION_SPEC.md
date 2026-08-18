# DISTRIBUTION_SPEC v1 — installers, first run, pairing, remote access

> **Historical V1 architecture.** Server distribution, pairing, discovery, and
> remote access are not part of the current V2 product.

Goal: someone downloads one installer, answers one question ("where are your
photos?"), and has the full product. The server is a graduation, not a
prerequisite. Frozen 2026-07-12.

## Modes (final model)

| Mode | How it's selected | What it means |
|---|---|---|
| `hub` | `AZIMUTH_MODE=hub` (default when unset and no hub URL) | Always-on library server. Owns originals + master catalog. |
| `standalone` | No `AZIMUTH_HUB_URL` set, `AZIMUTH_MODE=satellite` or `standalone` | Full local library. Satellite semantics (local imports, local everything) with the sync worker idle. **This is the desktop app's default.** |
| `satellite` | `AZIMUTH_HUB_URL` set | Standalone + sync: mirrors the hub catalog, uploads by content hash, oplog convergence. |

Rules:
- `standalone` is not a new code path — it is satellite mode with sync
  disabled because there is no hub. `is_satellite_mode()` stays true for it.
  A new `has_hub()` predicate gates the sync worker, mirror, prefetch, and
  sync chip. Nothing else changes.
- **Upgrading standalone → satellite happens at runtime** (user pairs with a
  hub in Settings): persist the hub URL + device token in the settings table,
  start the sync worker without restart. On next boot the env-less instance
  reads the stored hub URL.
- A standalone library that pairs with a **fresh** hub seeds it: the existing
  upload path already does this (everything is "not on hub yet"). No new code,
  but it must be proven by test.

## Artifact 1 — Azimuth Photo Server

### Frozen binary
- PyInstaller **onedir** build (onefile startup cost is unacceptable) of
  `web/app.py` + all base deps (rawpy, tifffile, imagecodecs, PIL, numpy,
  fastapi/uvicorn, zeroconf). **No torch, no AI models in the artifact.** AI
  workers already self-skip when deps are absent; that is the contract.
- Build script: `scripts/build_server.py` → `dist/azimuth-server/`.
  Must run on Linux and Windows (the Tauri app bundles the Windows build as
  its sidecar).
- Entrypoint honors all existing `AZIMUTH_*` env vars; with none set it
  uses platform-default data dirs (existing `runtime_paths` behavior) and
  serves on :8000.

### Docker image
- `Dockerfile` at repo root. `python:3.12-slim`, base requirements only,
  non-root user. Volumes: `/photos` (originals, read-write), `/data`
  (catalog + caches → `AZIMUTH_HOME=/data`). Expose 8000. HEALTHCHECK
  on the existing health endpoint.
- `docker-compose.yml` example at repo root (photos volume, data volume,
  restart unless-stopped).
- `deploy/unraid-template.xml` + `docs/INSTALL.md` sections for Synology
  Container Manager and TrueNAS.
- mDNS caveat documented: host networking mode recommended so discovery works.

## Artifact 2 — Azimuth Photo (the app)

- Tauri shell (`desktop/`) switches from the hardcoded dev venv to a bundled
  sidecar: `dist/azimuth-server/` shipped inside the app resources.
  Config/paths must come from a small JSON the shell reads
  (`%APPDATA%/azimuth/shell.json`), not compile-time constants — dev
  machines can point it at a venv, installed apps use the sidecar.
- First launch with no library: open straight into the first-run wizard (below).
- Auto-update: wire the Tauri updater config but leave signing keys/endpoint
  as documented TODOs (needs a decision on hosting).

## First-run wizard

One route, both faces: `/setup`. Server-rendered page in the existing design
system. Shown when the instance has zero catalog sources and setup has never
been completed (settings flag `setup_completed`). Never shown again after.

Design (match the app: dark, `--panel` card centered on `--bg`, `--r-card`
radius, existing button/input idioms — read the CSS variables, invent nothing):
1. **Welcome** — wordmark, one sentence, single primary button ("Choose your
   photos folder").
2. **Folder pick** — native dialog via Tauri when in the shell; plain path
   input + server-side folder browser otherwise (hub in Docker: text field
   with `/photos` prefilled). Multiple folders addable, one is enough.
3. **Import** — kicks off the existing source scan; live progress (count
   found / thumbed) reusing existing status endpoints; the grid is one click
   away immediately — do not block on completion.
4. **Optional extras card** (shown once import is running): "AI features"
   (semantic search, faces, auto-cull) with a download-on-demand button that
   pip-installs into a managed venv under `AZIMUTH_HOME` or, in Docker,
   points at the `-ai` variant docs. Skippable, default off.

No other questions. Library name, ports, cache budgets — all defaults,
changeable in Settings later.

## Pairing (device linking)

- Hub Settings → Devices panel: "Link a device" → hub generates a one-time
  8-char code (crypto random, 10-min TTL, single use) and shows it as both
  text and a QR encoding `{hub_url, code}`.
- New device (app settings → "Connect to server", or wizard's final card if a
  hub was discovered): enter code or scan → `POST /api/pair {code, device_name,
  platform}` → hub returns `{device_token, hub_id}`. Token stored in the
  satellite settings table; sent as `X-Device-Token` on sync calls.
- Hub keeps a `devices` table (id, name, platform, token hash, created,
  last_seen, revoked). Devices panel lists them with revoke buttons.
- **Auth enforcement is a later phase.** Pairing establishes identity and
  revocation now; hub endpoints stay open on the LAN/tailnet as today. An
  optional "require device token for sync" toggle is fine; default off.

## Discovery (mDNS)

- Hub announces `_azimuth._tcp.local` (name = configured library name or
  hostname, port, hub_id) via `zeroconf` — a worker-thread announce, hub mode
  only, env-disableable.
- Satellite/app: `GET /api/discover` does a 2s browse and returns found hubs.
  Settings "Connect to server" and the wizard's final card surface them:
  "Found *SEAN-NAS* on your network — Connect". Manual URL entry always
  available beneath.

## Remote access (Tailscale, guided)

- No relay infrastructure of ours, ever. First-class Tailscale guidance
  instead: Settings → Remote access panel detects `tailscale` on PATH and its
  status via `tailscale status --json`, then walks through: install link →
  `tailscale up` → one-click apply of `tailscale serve` per docs/FIELD_HTTPS.md
  (hub runs the command itself on user confirm) → shows the resulting
  https://…ts.net URL + QR for the phone.
- Satellite hub-URL field accepts the ts.net URL; document that HTTPS enables
  the mobile PWA + service worker.

## Non-goals (v1)

- No account system, no cloud relay, no payment/licensing anything.
- No macOS/Linux desktop installers yet (the code must not preclude them;
  the Windows Tauri build is the only shell target now).
- No auth walls on the hub beyond the optional sync-token toggle.
- No auto-update key infrastructure (config stubs + docs only).

## Acceptance (each lane proves its own)

- Frozen binary: `dist/azimuth-server/azimuth-server --help` runs on
  a machine with no Python; serves /d; imports a folder.
- Docker: `docker compose up` → healthcheck green → /setup renders → import a
  mounted folder → grid shows it.
- Wizard: fresh DB → /setup → pick folder → import starts → `setup_completed`
  set → /d never redirects to /setup again.
- Pairing: code round-trip test (satellite gets token, appears in devices
  table, revoke kills sync auth when toggle is on).
- Discovery: hub announce visible to `zeroconf` browse in a test; /api/discover
  returns it.
- Standalone-seeds-hub: property test — standalone with N images pairs with
  empty hub → hub ends with N originals + metadata converged.
