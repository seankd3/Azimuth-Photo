# Build the Azimuth Photo Windows app

Azimuth Photo is one local desktop process. Python owns the catalog and product
verbs; pywebview supplies the native WebView2 window and direct JavaScript
bridge. There is no server, port, child engine, or browser address.

The frozen directory includes the Python runtime, the one inlined UI document,
the V2 schema, and its native dependencies. A person running it does not install
Python, Node, Rust, or a web server.

## Build the unsigned app

Build on Windows with Python 3.12 and Node 22 available. The script installs the
pinned Python packages and exact JavaScript dependency before freezing the app.

From the repository root:

```powershell
.\scripts\build_windows_desktop.ps1
```

The final line prints the executable path:

```text
dist\azimuth-photo\azimuth-photo.exe
```

The folder is a testable unsigned artifact, not an installer. Signing, an
installer, and automatic updates belong after the clean-machine flow passes.

## First-install smoke

Use a Windows account with no existing Azimuth Photo data:

1. Launch `azimuth-photo.exe` without a terminal.
2. Confirm the welcome screen appears and no browser address or engine window
   is shown.
3. Use the native chooser to select a local photo folder; confirm scanning
   begins and the library opens.
4. Quit; confirm no Azimuth process remains and the catalog can be renamed.
5. Relaunch; confirm the same library opens without setup repeating.
6. Repeat with a mapped drive and then a UNC-backed folder.

App data belongs under the platform paths resolved by `core/runtime_paths.py`.
Originals remain in the selected folders.
