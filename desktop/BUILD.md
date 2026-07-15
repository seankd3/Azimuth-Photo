# Building Azimuth Photo desktop

Tauri v2 shell that spawns the local satellite server and wraps
`http://127.0.0.1:8010/d` in a native window with a sync tray.

## Prerequisites

- **MSVC toolchain** — Visual Studio Build Tools with the "Desktop development with C++"
  workload (provides `link.exe` + Windows SDK). This is the only missing piece; Rust
  (`rustup` with the `x86_64-pc-windows-msvc` target) is already installed.
- **WebView2 runtime** — preinstalled on Windows 11.
- The field server venv at `photoarchive-field\web\.venv` (already set up per
  `photoarchive-field\FIELD_README.md`).

## Compile check (first thing once MSVC lands)

```powershell
cd C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-desktop\src-tauri
cargo check
```

## Run in dev

```powershell
cd C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-desktop\src-tauri
cargo run
```

Dev builds open devtools automatically. The app spawns the satellite server itself —
don't start one manually, or do: if :8010 is already serving, the shell skips the spawn
and just connects.

## Release build

```powershell
cd C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-desktop\src-tauri
cargo build --release
```

Exe lands at: `src-tauri\target\release\photoarchive-desktop.exe`

## NSIS installer

Needs the Tauri CLI once: `cargo install tauri-cli --locked` (or `npx @tauri-apps/cli`).

```powershell
cd C:\Users\smast\OneDrive\Desktop\Projects\photography\photoarchive-desktop\src-tauri
cargo tauri build
```

Outputs:
- Exe: `src-tauri\target\release\photoArchive.exe`
- Installer: `src-tauri\target\release\bundle\nsis\photoArchive_0.1.0_x64-setup.exe`
  (per-user install, no admin needed — `installMode: currentUser`)

## Shell config (`%APPDATA%\photoarchive\shell.json`)

The desktop shell no longer hard-requires the compile-time venv paths. On startup
it reads `%APPDATA%\photoarchive\shell.json` (created by the installer or by hand
for dev machines). Missing keys fall back to the historical constants in
`src-tauri/src/server.rs`.

```json
{
  "python": "C:\\Path\\To\\photoarchive-server\\python.exe",
  "server_cwd": "C:\\Path\\To\\photoarchive-server",
  "env": {
    "PHOTOARCHIVE_MODE": "standalone",
    "PHOTOARCHIVE_HOME": "C:\\PhotoArchive",
    "PHOTOARCHIVE_PORT": "8010"
  }
}
```

- `python` — interpreter that can run `python -m uvicorn app:app`
- `server_cwd` — working directory for that process (the `web/` tree or the
  frozen sidecar folder that contains `app`)
- `env` — optional overrides merged onto the default satellite/standalone env
  map (override individual keys; omit `env` to keep the built-in defaults)

Installed builds point `python` / `server_cwd` at the bundled
`dist/photoarchive-server/` sidecar. Dev machines can point them at a checkout
venv without rebuilding the shell.

## What the shell does at runtime

1. Shows a splash (`ui/index.html`), probes `http://127.0.0.1:8010/`.
2. If nothing is serving, spawns the configured Python with
   `-m uvicorn app:app --host 127.0.0.1 --port 8010` (cwd + env from
   `shell.json`, else the fallback satellite profile in `server.rs`).
3. Polls readiness up to 45 s, then navigates to `http://127.0.0.1:8010/d`.
4. Tray: Open / sync status (polled every 30 s) / Pause–Resume sync / Quit.
   The spawned server is killed on app exit; an externally started server is left alone.
5. Single instance: relaunching focuses the existing window.
