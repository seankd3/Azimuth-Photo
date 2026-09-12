# Releasing Azimuth Photo V2

V2 does not yet have a public installer. Tagged releases currently produce one
unsigned Windows application directory so the clean-machine flow can be tested
without pretending signing or updates are finished.

## Before a tag

1. Run the one check from the repository root: `./scripts/azimuth-check`
   (lint, the gates, the suite, the node specs, the ledger).
2. Run `npm audit`.
3. Run `.\scripts\build_windows_desktop.ps1`, which is what
   `.github/workflows/release.yml` runs through `scripts/build_desktop.py`.
4. Launch the frozen executable with an empty `AZIMUTH_HOME`.
5. Open the native folder chooser, attach a real photo folder, and verify the
   progressive grid, direct first-to-last keyboard navigation, details, and
   keyboard navigation inside the loupe. A realistic large-catalog proof must
   keep the rendered photo controls bounded rather than accumulating pages.
6. Quit and verify no Azimuth process remains and the catalog can be renamed.
7. Perform the same flow on a clean Windows machine before calling the artifact
   releasable.

## Create the artifact

Update `VERSION`, commit the release preparation, and push an annotated `v*`
tag. `.github/workflows/release.yml` builds the one-process app on Windows,
zips `dist/azimuth-photo/`, and attaches the zip to a generated GitHub release.

The artifact name is:

```text
azimuth-photo-windows-<tag>.zip
```

The workflow does not publish Docker images, Linux servers, Tauri bundles, or
child engines.

## Work still required before a public release

- a per-user installer and clean uninstall;
- code signing and reputation testing;
- version metadata embedded in the executable;
- update design, rollback, and signed-manifest proof;
- clean-machine WebView2 and RAW-decoder verification;
- cross-platform packaging only after the Windows product is coherent.

None of those jobs may reintroduce a server, port, or second process.
