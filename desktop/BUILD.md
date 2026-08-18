# Build the Azimuth Photo Windows app

The Windows app is a self-contained V2 package. It includes the frozen private
engine, opens the first-folder experience, and stores its catalog and generated
data in the normal Windows application-data folders. A person installing it
does not install Python or configure a server.

This package is real but not yet the final desktop architecture: Tauri owns one
private loopback engine process. `CORE.md` requires that transport to disappear
before Desktop packaging becomes Proven. Until that collapse lands, this build
is the executable V2 integration path and nothing in it may call V1.

## Build an unsigned test installer

On Windows, install Python 3.12, the MSVC desktop toolchain, and Rust. The build
script installs the pinned V2 Python build set and the pinned Tauri CLI when
they are absent.

From the repository root:

```powershell
.\scripts\build_windows_desktop.ps1
```

The script:

1. verifies `VERSION`, Cargo, and Tauri versions agree;
2. builds `dist\azimuth-server\azimuth-server.exe` and its supporting
   onedir files;
3. bundles that complete directory into the desktop app;
4. creates a per-user NSIS installer without requiring administrator access.

The final line prints the installer path under:

```text
desktop\src-tauri\target\release\bundle\nsis\
```

This lane intentionally does not enable signing or automatic updates. Those
belong after the unsigned install-to-library flow passes on a clean Windows
machine.

## First-install smoke

Use a Windows account with no existing Azimuth Photo data:

1. Install and launch Azimuth Photo without a terminal.
2. Confirm the welcome screen appears and no browser address or engine window
   is shown.
3. Choose a local photo folder; confirm scanning begins and the library opens.
4. Repeat with a mapped NAS drive.
5. Enter a UNC share such as `\\NAS\Photos`; confirm photos appear.
6. Quit and relaunch; confirm the same library opens without setup repeating.

App data belongs under `%LOCALAPPDATA%\Azimuth Photo` and
`%APPDATA%\Azimuth Photo`. Originals remain in the selected folders.

## Developer override

A developer may run the shell against another frozen engine by setting
`AZIMUTH_DESKTOP_ENGINE_PATH` to the executable before `cargo run`. This is a
development seam only; installed builds resolve the bundled engine resource.
