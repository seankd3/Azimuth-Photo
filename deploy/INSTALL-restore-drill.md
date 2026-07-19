# Install pa-restore-drill (CTO session — do not enable from the feature lane)

Units are committed under `deploy/`. They are **not** enabled by this work order.

```bash
# From the photo-archive checkout that owns prod (after this branch lands):
sudo install -m 644 deploy/pa-restore-drill.service /etc/systemd/system/pa-restore-drill.service
sudo install -m 644 deploy/pa-restore-drill.timer   /etc/systemd/system/pa-restore-drill.timer
sudo systemctl daemon-reload
sudo systemctl enable --now pa-restore-drill.timer
systemctl list-timers pa-restore-drill.timer
```

Manual one-shot (no timer, no ntfy unless `--notify`):

```bash
./scripts/restore_drill.py
# or with transition alerts:
./scripts/restore_drill.py --notify
```

State / log (same pattern as `pa-watchdog`):

| Path | Role |
|------|------|
| `/var/tmp/pa-restore-drill.state` | `ok` / `bad` — ntfy only on transitions |
| `/var/tmp/pa-restore-drill.log` | bounded run log (last 5000 lines) |
| `http://127.0.0.1:8091/azimuth-alerts` | ntfy topic |

The service points at `/home/sean/Projects/photo-archive` (prod checkout). Merge this branch there before enabling the timer.
