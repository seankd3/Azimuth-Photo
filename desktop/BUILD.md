# Build the Azimuth Photo Windows app

> **Transitional implementation guide.** This builds the current V1 Tauri
> shell. V2 intends one desktop process and does not preserve Tauri or the local
> HTTP boundary as product architecture.

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
5. Type or choose a UNC share such as `\\NAS\Photos`; confirm photos appear.
6. Quit and relaunch; confirm the same library opens without setup repeating.

App data belongs under `%LOCALAPPDATA%\Azimuth Photo` and
`%APPDATA%\Azimuth Photo`. Originals remain in the selected folders.

## Open an existing library

A first install keeps its own data under `%LOCALAPPDATA%\Azimuth Photo`. A
person who already has a library keeps that library instead: write a pointer
file at `%APPDATA%\Azimuth Photo\library.json` before the first launch.

```json
{
  "data_root": "C:\\Azimuth Photo",
  "mode": "satellite",
  "preview_root": "C:\\Azimuth Photo\\thumbs"
}
```

All three fields are optional. Each field that is present replaces one engine
default: `data_root` selects the library folder, `mode` selects `standalone`,
`satellite`, or `hub`, and `preview_root` selects the preview cache. A missing,
empty, or damaged pointer file keeps the packaged defaults, so a bad edit can
never stop the app from starting.

## Developer override

A developer may run the shell against another frozen engine by setting
`AZIMUTH_DESKTOP_ENGINE_PATH` to the executable before `cargo run`. This is a
development seam only; installed builds resolve the bundled engine resource.
