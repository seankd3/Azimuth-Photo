# photoArchive — Field Satellite (Windows laptop)

Runs the full app locally in **satellite mode**: your entire hub library is
mirrored here, imports/culls/edits happen at local speed, and everything syncs
back to the hub automatically. Big picture: [docs/TOPOLOGY.md](docs/TOPOLOGY.md).

## Start

From `web/` (Git Bash):

```bash
PHOTOARCHIVE_MODE=satellite \
PHOTOARCHIVE_HUB_URL='http://100.102.150.104:8000' \
PHOTOARCHIVE_HOME='C:\PhotoArchiveField' \
PHOTOARCHIVE_THUMB_CACHE_DIR='C:\PhotoArchiveField\thumbs' \
PHOTOARCHIVE_DEVELOP_CACHE_DIR='C:\PhotoArchiveField\develop' \
PHOTOARCHIVE_EXPORT_DIR='C:\PhotoArchiveField\exports' \
./.venv/Scripts/python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

Then open http://127.0.0.1:8010/d — or use the Tauri tray app (`desktop/`)
which runs this for you.

Do **not** set `PHOTOARCHIVE_SMOKE_MODE` — it's a test-only flag that disables
DB init and all background workers (thumbnails never generate, sync never runs).

## Stop

```powershell
Get-NetTCPConnection -LocalPort 8010 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## Where things are

- Repo: this folder (origin = omarchy over ssh; branch `main`)
- Venv: `web\.venv` (Python 3.12; base requirements + tifffile/imagecodecs + CPU torch + ai-search)
- All data: `C:\PhotoArchiveField\` (catalog DB, thumbs, develop cache, exports, server.log)
- Card offload staging: `D:\CardOffload\`

## Update to the latest hub code

```bash
git pull   # after a deploy on omarchy, main has the new build
```

## Windows gotchas

- Keep all import sources on one drive per source (multi-drive fixes landed,
  but D:\ staging → import is the proven path).
- Sync/AI workers self-skip if their deps are absent; embeddings can hold
  write locks — writes retry, but if the app feels locked up, it's usually that.
