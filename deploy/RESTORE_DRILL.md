# Azimuth Photo restore drill

The weekly drill restores the newest sealed catalog snapshot into disposable
storage, validates it, checks sample image rows, and removes the restored copy.

```bash
sudo install -m 644 deploy/azimuth-restore-drill.service \
  /etc/systemd/system/azimuth-restore-drill.service
sudo install -m 644 deploy/azimuth-restore-drill.timer \
  /etc/systemd/system/azimuth-restore-drill.timer
sudo systemctl daemon-reload
sudo systemctl enable --now azimuth-restore-drill.timer
systemctl list-timers azimuth-restore-drill.timer
```

Manual one-shot (no timer, no ntfy unless `--notify`):

```bash
./scripts/restore_drill.py
# or with transition alerts:
./scripts/restore_drill.py --notify
```

State and log:

| Path | Role |
|------|------|
| `/var/tmp/azimuth-restore-drill.state` | `ok` / `bad` — ntfy only on transitions |
| `/var/tmp/azimuth-restore-drill.log` | bounded run log (last 5000 lines) |
| `http://127.0.0.1:8091/azimuth-alerts` | ntfy topic |

The service points at the canonical `/home/sean/Projects/azimuth-photo`
checkout. Enable it only after that checkout and its runtime configuration
have passed the production health check.
