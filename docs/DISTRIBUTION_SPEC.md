# V2 Distribution Shape

This is the current distribution contract. The rejected V1 server, Docker,
pairing, discovery, remote-access, and Tauri-sidecar design remains available
in Git history; it is not an alternative V2 mode.

## Product artifact

Azimuth Photo ships as one local desktop application. The Windows artifact is
currently a PyInstaller onedir bundle containing:

- the Python runtime and V2 product modules;
- one pywebview/WebView2 native window;
- the five-table schema;
- one bundled and inlined UI document;
- Pillow and rawpy decoding support;
- one canonical Windows executable icon.

It contains no HTTP framework, listening socket, child engine, Rust shell,
remote host configuration, browser authentication, or V1 catalog selector.

## First run

A fresh catalog opens directly into one question: “Point me at your photos.”
The answer is collected with the operating system's folder chooser. Attach is
immediate; scanning is progressive; browsing remains responsive while the
folder is read. There is no setup route or server-side folder browser.

## Ownership and safety

The app owns its local catalog, cache, native window, and worker lifecycle.
Closing the window drains admitted work, stops the derivative worker, releases
SQLite, and leaves no child process because none exists.

Original photographs remain in their selected folders. The catalog is always
local and never placed on a network share. Mapped and UNC-backed folders are
drives under the same marker/copy model; their clean-machine product flow still
requires explicit proof before release.

## Build and release

`scripts/build_desktop_ui.py` produces the serverless document.
`scripts/build_desktop.py` freezes the app. The supported Windows entry point is:

```powershell
.\scripts\build_windows_desktop.ps1
```

Tagged CI runs the same Python and JavaScript recipes and publishes an unsigned
zip. An installer, signing, updates, rollback, and other operating systems are
deliberate later distribution work.

## Acceptance

Distribution is Proven only when a clean Windows account can:

1. launch without Python, Node, a terminal, or a network listener;
2. see the first-folder experience;
3. attach local, mapped, and UNC-backed photo folders through the native chooser;
4. browse progressively during a real scan;
5. quit with no remaining process and an immediately releasable catalog handle;
6. relaunch into the same library;
7. uninstall without touching originals or user-owned decisions.

The present unsigned artifact has proved the source and frozen one-process
launch, native chooser, real V2 browsing, clean exit, and catalog release on the
development XPS. Clean-machine install/uninstall and cross-platform proof remain.
