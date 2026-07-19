# Client Auto-Update — "The hub carries its clients" (frozen spec, Fable 2026-07-17)

## Product intent

A satellite install must never be stale and must never ask the user about versions. One deploy to the hub updates the entire fleet on next contact. Version skew stops existing as a user-visible concept. (Origin: the XPS field install silently drifted 4 commits behind and nobody noticed — that class dies here.)

## Scope

- IN: the Python satellite/field install (Windows + Linux), updating itself from its hub.
- OUT: the web UI (already hub-served, always fresh), the Android APK (own distribution), the hub updating itself (deploys stay explicit via Release).

## Mechanism — hub-served bundles, no GitHub dependency

Field LANs may have no internet. The satellite updates FROM THE HUB, which serves its own running tree:

1. **Hub advertises identity.** `GET /api/version` returns `{sha, bundle_sha256, schema_version}` of the running checkout. Cached at startup; no git calls per request.
2. **Hub serves its tree.** `GET /api/client/bundle` returns a tar.gz of the hub's own checkout at its running sha (`git archive` produced once, cached on disk beside the thumb cache, regenerated only when sha changes). Endpoint sits behind existing device/owner auth like every sync route. Open-trust LANs (device auth off) serve client code openly to anyone who can reach the hub.
3. **Satellite converges on connect.** During the normal sync-contract handshake the satellite compares hub sha to its own. On mismatch:
   - download bundle → verify sha256 against `/api/version` → unpack to `versions/<sha>/` beside the install
   - deps: hash `requirements.txt`; reuse the existing venv when unchanged (normal case), else build `venvs/<deps-hash>/` fresh
   - **flip**: write `current.txt` pointing at the new version dir (atomic rename of the pointer file — works on Windows; no symlinks)
   - restart itself ONLY at a safe point: never mid-upload/mid-import; drain or checkpoint in-flight sync first (resumable uploads already survive restarts by design)
4. **Launcher reads the pointer.** The satellite launch script resolves `current.txt` → version dir and starts from there. Keep the previous version dir; on two consecutive failed boots of the new version (crash-before-ready), the launcher flips the pointer back and reports the rollback in the satellite UI status.
5. **Satellite DB migrations** run at boot exactly as today (premigrate backup + the new verified-backup pipeline apply unchanged).

## UX rules

- Zero prompts, zero version numbers in the primary UI. The presence indicator may show "Updating…" for the seconds it takes; that is all.
- Update failures are quiet and safe: satellite keeps running the old version, retries next handshake, surfaces one line in the sync status ("Update available — will retry"), never blocks sync itself. EXCEPTION: if the hub refuses the old contract entirely (breaking schema), satellite pauses sync with an honest status rather than half-talking.
- Never update while the user is mid-task in the satellite UI (active import/develop session); defer to next idle handshake.

## Invariants

- Bundle verification is mandatory: sha256 mismatch → discard, keep current, retry later. Never unpack unverified bytes.
- The flip is atomic and reversible; at every instant `current.txt` points at a complete, verified tree.
- Old version dirs: keep exactly one previous; prune older (bounded disk).
- The satellite always runs a sha the hub has actually run (bundles only come from a live hub) — no forward versions, no third-party source.

## Acceptance (e2e, scratch pattern)

1. Scratch hub at sha A, satellite install at older sha B, both live: satellite handshakes → downloads → flips → restarts → reports sha A; sync resumes; an in-flight resumable upload started under B completes under A.
2. Corrupt bundle (bit-flip mid-file): satellite discards, stays on B, status shows retry line, next good handshake succeeds.
3. Crash-looping new version (inject a boot failure): launcher auto-rolls back to B, satellite reports rollback, sync continues on B.
4. requirements.txt unchanged → no venv rebuild (assert by mtime/marker); changed → new venv built and used.
