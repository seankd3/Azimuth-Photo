# Azimuth Photo — Field Satellite (Windows laptop)

Runs the full app locally in **satellite mode**: your entire hub library is
mirrored here, imports/culls/edits happen at local speed, and everything syncs
back to the hub automatically. Big picture: [docs/TOPOLOGY.md](docs/TOPOLOGY.md).

## Start

From the repo (PowerShell):

```powershell
.\scripts\start_azimuth_windows.ps1
```

The launcher selects the real `C:\Azimuth Photo` catalog, reuses the running
server when possible, waits until it is healthy, and opens the desktop surface
in an app window at http://127.0.0.1:8010/d.

Do **not** set `AZIMUTH_SMOKE_MODE` — it's a test-only flag that disables
DB init and all background workers (thumbnails never generate, sync never runs).

## Stop

```powershell
Get-NetTCPConnection -LocalPort 8010 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## Where things are

- Repo: `C:\Users\smast\OneDrive\Desktop\Projects\photography\azimuth-photo`
  (`main`, GitHub origin)
- Venv: `web\.venv` (Python 3.12; base runtime + development tools; optional
  AI packs are installed separately)
- Runtime data: `C:\Azimuth Photo\` (catalog, previews, Develop cache,
  models, logs, and transfer receipts)
- Originals waiting for verified hub offload: the configured XPS source
  folders, including `C:\Pictures`

## Update

```bash
git pull --ff-only
```

## Windows gotchas

- Let the catalog scan finish before starting **Free up space**. Only files
  represented by a verified sync identity can be removed locally.
- Sync/AI workers self-skip if their deps are absent; embeddings can hold
  write locks — writes retry, but if the app feels locked up, it's usually that.
