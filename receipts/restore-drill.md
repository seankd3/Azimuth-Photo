# Restore drill — work order report

## Verdict

**Done.** Weekly restore drill lands on `restoredrill`: picks the newest sealed snapshot, restores into throwaway scratch, reuses backup verify helpers, spot-checks random rows, cleans up always. Systemd unit+timer committed under `deploy/` — **not enabled** on the host.

## What shipped

| Piece | Path |
|-------|------|
| Module | `web/features/system/restore_drill.py` |
| CLI | `scripts/restore_drill.py` |
| Tests | `web/test_restore_drill.py` (pytest, real exit codes) |
| Service | `deploy/pa-restore-drill.service` |
| Timer | `deploy/pa-restore-drill.timer` (Sun 04:30 + up to 2h random delay) |
| Install | `deploy/INSTALL-restore-drill.md` |

### Behavior

1. Newest sealed `photoarchive-*.db.gz` under the runtime backup root (or `--backup-root` / `--snapshot`).
2. Scratch under `--scratch-parent` (default system temp) or exact `--scratch`.
3. **Hard refuse** (exit 2) if scratch already contains `.photoarchive-backup-owner` or `photoarchive.db` — refused targets are never deleted.
4. Verify via existing helpers: `_validate_restored_catalog`, `catalog_quick_check`, `_image_count`, `_verify_backup_artifact` (self-consistency).
5. Spot-check N random `images` rows (filename/filepath/elo/date_taken) + `develop_settings.settings` JSON when present.
6. Scratch removed on success and failure.
7. `--notify`: state file + bounded log + ntfy on transitions only (same shape as `pa-watchdog`).

Exit codes: `0` ok, `1` drill/verify failure, `2` prod-dir refusal.

## Tests

```text
cd web && .venv/bin/python -m pytest test_restore_drill.py -v
# 4 passed in ~1s
# success → 0, corrupt → 1, owner-marker/live-db refuse → 2 (target preserved), newest-wins
```

## E2E against prod (read-only w.r.t. snapshot)

```text
$ ./scripts/restore_drill.py \
    --backup-root /mnt/expansion/PhotoArchiveCache/backups \
    --scratch-parent /var/tmp \
    --spot-rows 5 --seed 42

ok snapshot=photoarchive-20260717-040000.db.gz images=167 spot_checked=5 scratch=/var/tmp/pa-restore-drill-3nwvvmrx
exit_code=0
elapsed_sec=3.418
scratch cleaned
owner marker intact
```

Prod snapshot file and `.photoarchive-backup-owner` were not modified. Timer was **not** enabled.

## CTO install (after review)

See `deploy/INSTALL-restore-drill.md`. Points at `/home/sean/Projects/photo-archive` once this branch merges there.
