# SEQPLAN — one bulk HDD stream at a time

Lane: `gxseq` (worktree off `develop`). Uncommitted by design.

## Problem (measured)

With preview pregen and cloud vault rclone both reading `/mnt/expansion` (20TB
exFAT HDD), effective read speed collapsed to ~2.57 MB/s (~7.3s per ~9MB
original) and pregen fell to ~9 thumb files/min. Stopping the vault restored
pregen throughput. Spinning disks want one sequential stream.

## Design

Own the policy in one place: `web/core/bulk_scheduler.py`.

- Does **not** change `hdd_governor` single-flight bulk-read gate.
- One knob: `PHOTOARCHIVE_BULK_SEQUENCING` (default `1` / on).
- Desired-running flags persist in existing runtime `state_dir` as
  `bulk_desired.json` (same persistence family as `cloud_backup_status.json`).
- Vault status while yielding: `state=waiting`, message
  `yielding disk to preview build`.
- Explicit `POST /api/backup/cloud/start` = owner override → run immediately with
  warning that both streams share the disk.
- Nightly / auto-resume starts respect sequencing (may wait).

### Ownership

| Concern | Module |
|---|---|
| Decision + desired flags | `core/bulk_scheduler.py` |
| Vault wait / override / status | `features/backup/cloud.py` |
| Manual start = override | `features/backup/routes.py` |
| Pregen desired on start/stop | `thumbnails/__init__.py` |
| Configure probe + auto-resume | `core/background.py` |
| Waiting badge in settings UI | `static/js/desktop/cloud_backup.js` |

### When do previews “hold” the disk?

`manual_mode` and not `manual_pause`, and pregen `state` in `{running, waiting}`.

`complete` / `idle` / `paused` / `error` / disabled → vault may run.

## Behavior table

| Sequencing | Previews hold disk | Vault desired | Manual override | Vault action | Status |
|---|---|---|---|---|---|
| on | yes | yes | no | wait | `waiting` — yielding disk to preview build |
| on | no | yes | no | run | `running` |
| on | yes | — | yes (POST start) | run | `running` + share-disk warning |
| on | — | no | no | idle | `idle` |
| off | yes | yes | no | run | `running` (no yield) |

### Auto-resume on app startup

1. If `bulk_desired.json` has `pregen: true` → `start_pregeneration()`.
2. Shortly after, if `vault: true` → `start_sync(manual_override=False)`
   (may enter `waiting` if previews hold the disk).
3. Vault finish/error clears `vault` desired (one-shot sync). Stop clears it.
   Pregen stop clears `pregen` desired.

## Test results

```text
cd web && .venv/bin/python -m pytest -x -q \
  test_bulk_scheduler.py test_cloud_backup.py test_hddgov.py \
  test_cache.py test_settings_status.py test_modular_contracts.py

72 passed
```

Core scheduler cases covered in `test_bulk_scheduler.py`:

- previews pending → vault waits
- previews done → vault runs
- manual override runs while pending
- sequencing off ignores pending
- restart reloads desired flags from disk

Integration covered in `test_cloud_backup.py`:

- scheduled start stays `waiting` while previews pending
- manual override runs with share-disk warning
