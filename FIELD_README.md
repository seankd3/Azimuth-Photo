# Azimuth Photo — Windows satellite

A Windows computer can run the full app locally in satellite mode: its catalog
and previews stay fast on the laptop while changes and verified originals sync
to a hub.

## First run

Create the development environment described in
[docs/development.md](docs/development.md), then launch a fresh standalone
library:

```powershell
.\scripts\start_azimuth_windows.ps1
```

The default runtime lives under the current user's local application-data
directory. To use an existing portable runtime safely:

```powershell
.\scripts\start_azimuth_windows.ps1 `
  -DataRoot "D:\Azimuth Photo" `
  -Mode satellite `
  -PreviewRoot "D:\Azimuth Photo\cache\previews" `
  -RequireExistingCatalog
```

The launcher refuses to create a replacement catalog when
`-RequireExistingCatalog` is set, reuses a healthy process from the same
checkout, and opens http://127.0.0.1:8010/d. A desktop shortcut may pass the
same explicit options.

Do not set `AZIMUTH_SMOKE_MODE`; it disables normal initialization and
background workers for isolated tests.

## Storage behavior

Keep the catalog, previews, Develop cache, models, logs, and transfer receipts
on fast local storage. Original source folders are user-selected. After a hub
has accepted an upload and verified its complete hash, **Free up space** can
remove the matching local original while retaining the catalog and previews.

Let the catalog scan finish before using **Free up space**. Only files with a
verified sync identity are eligible.
