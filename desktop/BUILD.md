# Build the Azimuth Photo Windows app

The Windows app is a self-contained Tauri package. It includes the frozen
Azimuth Photo engine, opens the real first-run experience, and stores its
catalog and generated data in the normal Windows application-data folders.
Customers do not install Python or configure a URL.

## Build an unsigned test installer

On Windows, install the MSVC desktop toolchain, Rust, and the Tauri CLI:

```powershell
cargo install tauri-cli --locked
```

From the repository root:

```powershell
.\scripts\build_windows_desktop.ps1
```

The script:

1. verifies `VERSION`, Cargo, and Tauri versions agree;
2. builds `dist\photoarchive-server\photoarchive-server.exe` and its supporting
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
5. Type or choose a UNC share such as `\\NAS\Photos`; confirm photos appear.
6. Quit and relaunch; confirm the same library opens without setup repeating.

App data belongs under `%LOCALAPPDATA%\Azimuth Photo` and
`%APPDATA%\Azimuth Photo`. Originals remain in the selected folders.

## Developer override

A developer may run the shell against another frozen engine by setting
`AZIMUTH_DESKTOP_ENGINE_PATH` to the executable before `cargo run`. This is a
development seam only; installed builds resolve the bundled engine resource.
