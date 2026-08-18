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

Azimuth opens a native folder chooser. Select a folder containing photographs.
The folder is attached immediately and the library fills progressively while
it is read. Select “This is an archive drive” only for a durable archive copy.

Azimuth reads originals in place. Attaching a folder never moves, renames, or
deletes its photographs.

## Data locations

On Windows, the V2 catalog lives at:

```text
%LOCALAPPDATA%\Azimuth Photo\catalog\azimuth-v2.db
```

Generated tiles live under:

```text
%LOCALAPPDATA%\Azimuth Photo\cache\v2-tiles\
```

Set `AZIMUTH_HOME` before launch to isolate all V2 data under another directory.
This is useful for development and clean-catalog proof; it does not select a V1
catalog or compatibility mode.

## Run from source

After installing `web/requirements-v2.txt` into `web/.venv` and running
`npm ci`, use:

```powershell
.\scripts\start_azimuth_windows.ps1
```

Pass `-DataRoot C:\some\empty\folder` for an isolated source run. The launcher
builds the current modular UI and opens the same one-process desktop boundary
used by the frozen app.

The complete build and smoke procedure is in [`desktop/BUILD.md`](../desktop/BUILD.md).
