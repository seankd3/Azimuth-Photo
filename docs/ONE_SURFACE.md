# One Surface — hub, desktop, mobile, Android as a single library

**Goal.** A user owns one photo library. Every screen — desktop app (satellite), hub web, mobile PWA, Android — is a *window onto the same library*. Windows never block on each other, never disagree for long, never show a spinner that can't resolve, and never break because one of them updated first.

**Grade today (2026-07-15, measured):** foundations A−, seams C.
Identity (content-hash), oplog LWW convergence, mirror + read-through, offline queues: proven, elegant.
But: no version handshake (2-day-old hub silently breaks new desktop's Empty Trash — and would have purged the whole hub trash), foreground actions block on the WAN (30 s frozen dialogs), hub sickness is invisible (lock storm + pinned core for 2 days, nobody told), pending states sometimes render as eternal spinners.

## The four laws

1. **Local-first, always.** No foreground interaction may await the network. Every mutation applies to the local catalog instantly and enqueues an op; the sync worker reconciles in the background with backoff. If a remote leg fails, the UI stays correct and *honest* (pending badge), never frozen and never lying.

2. **Contract before conversation.** Every satellite⇄hub exchange is versioned. `GET /api/version` returns `{app_version, api_rev, capabilities[]}`; satellites send `X-PA-Api-Rev`. A peer that doesn't understand a request refuses it cleanly; the caller degrades gracefully and tells the user in product language ("The hub needs an update"), never guesses. Destructive ops (trash empty, permanent delete) REQUIRE capability confirmation first.

3. **Honest state, everywhere.** Every asynchronous fact has a visible truthful state: pending, syncing, failed-retrying, done. No spinner without a timeout and a fallback. Counts shown on two surfaces must come from the same converged source or be labeled ("syncing…"). A thumb that can't load renders a placeholder, not a spinner.

4. **Self-aware fleet.** Each node watches its own health (event-loop latency, DB write latency, worker starvation) and exposes it at /api/health; satellites surface hub health in the sync chip; sustained sickness alerts via ntfy (odysseus, omarchy:8091). A sick hub must degrade (pause workers), not silently rot for days.

## Workstreams

- **W1 hubhealth** (running): kill the SQLite lock storm — short transactions, busy retry on every write path, stop the hot loop. Then: standing rule + perf-budget test for write-path latency.
- **W2 trashux** (running): first full local-first retrofit (Empty Trash): local purge instant, hub leg queued+badged, legacy-hub guard, trash thumbs.
- **W3 handshake**: /api/version + capabilities on both ends, X-PA-Api-Rev header, destructive-op capability gate, sync-chip "hub needs update / hub unreachable / hub healthy" states, version-skew QA scenario.
- **W4 wan-audit**: sweep every route + UI action for inline WAN awaits or unbounded remote timeouts; convert offenders to the W2 pattern (queue + badge). Output: table of offenders → fix lane.
- **W5 health**: /api/health (event-loop lag, db write p95, worker heartbeats, queue depths) on hub + satellite; sync chip consumes it; ntfy alert when sustained-sick; hub watchdog pauses bulk workers under lock pressure.
- **W6 deploy-sanity**: hub runs main + one-command gated deploy (scripts/deploy.sh); after W1–W3 land: deploy → skew gone. Long-term: auto-update rings (hub Docker tag, desktop Tauri updater, Android APK) so surfaces can't drift apart for long.

## Acceptance ("does it feel like one thing?")
- Pull the hub's network cable mid-session: desktop keeps culling/editing/searching its 139k mirror at full speed; every hub-dependent verb shows honest pending; reconnect → everything drains, no user action.
- Update desktop two versions ahead of hub: nothing breaks; skewed features refuse with one clear sentence.
- Trash 50 photos on the phone, empty trash on desktop, view on hub web: all three agree within one sync cycle, no refresh clicking.
- No spinner anywhere can outlive 10 s without becoming a placeholder or an honest error.
