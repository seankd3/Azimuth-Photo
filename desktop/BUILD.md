# Building photoArchive desktop

Tauri v2 shell that spawns the local satellite server (`photoarchive-field`) and wraps
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

## What the shell does at runtime

1. Shows a splash (`ui/index.html`), probes `http://127.0.0.1:8010/`.
2. If nothing is serving, spawns
   `photoarchive-field\web\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010`
   (cwd `photoarchive-field\web`) with the satellite env profile
   (`PHOTOARCHIVE_MODE=satellite`, hub `http://100.102.150.104:8000`,
   `PHOTOARCHIVE_HOME=C:\PhotoArchiveField` + cache dirs, smoke mode on).
3. Polls readiness up to 45 s, then navigates to `http://127.0.0.1:8010/d`.
4. Tray: Open / sync status (polled every 30 s) / Pause–Resume sync / Quit.
   The spawned server is killed on app exit; an externally started server is left alone.
5. Single instance: relaunching focuses the existing window.
