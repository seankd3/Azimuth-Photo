# Run Azimuth Photo V2

V2 is currently a Windows desktop development build, not a public release. It
is one local process with no server, port, browser login, Docker container, or
mobile companion.

## Build the app

Install Python 3.12 and Node 22, then run from the repository root:

```powershell
.\scripts\build_windows_desktop.ps1
```

The command installs the exact V2 build dependencies and creates:

```text
dist\azimuth-photo\azimuth-photo.exe
```

Run that executable from its containing directory. The directory is a complete
unsigned test artifact; Python and Node are not required on the machine that
runs it. It is not yet an installer.

## First launch

Azimuth first asks where it should live: one folder that holds the catalog and
the previews, like a Lightroom catalog. It proposes an `Azimuth Photo` folder on
the fast internal disk with the most room; *Change…* opens the native chooser.
The choice is remembered, so later starts do not ask.

It then opens a native folder chooser for your photographs. Select a folder;
it is attached immediately and the library fills progressively while it is
read. Select “This is an archive drive” only for a durable archive copy.

Azimuth reads originals in place. Attaching a folder never moves, renames, or
deletes its photographs.

## Data locations

Everything the application makes for itself lives in the home you chose:

```text
<home>\catalog\azimuth.db
<home>\previews\
```

The one file outside the home is the pointer that names it, on Windows at
`%LOCALAPPDATA%\Azimuth Photo\home`.

Set `AZIMUTH_HOME` before launch to make that folder the home without reading
or writing the pointer. This is how development runs and clean-catalog proofs
stay isolated; it does not select a V1 catalog or compatibility mode.

## Run from source

After installing `web\requirements-v2.txt` into `web\.venv` and running
`npm ci`, use:

```powershell
.\scripts\start_azimuth_windows.ps1
```

Pass `-DataRoot C:\some\empty\folder` for an isolated source run. The launcher
builds the current modular UI and opens the same one-process desktop boundary
used by the frozen app.

The first-install smoke is in [`development.md`](development.md).
