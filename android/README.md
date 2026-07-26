# Azimuth Photo — Android

A native Google-Photos replacement for a self-hosted Azimuth Photo library. It is
your phone's photo app: it shows your camera roll, quietly backs every shot up to your
own server over Tailscale, lets you browse your entire archive (years of DSLR RAWs,
exported edits, everything) from anywhere, and can free space by aging backed-up media
off the device. No cloud account, no subscription, no one else's servers.

## Features

- **Camera-roll timeline** — day-grouped grid, pinch to change density (3–5 columns),
  fast-scroll scrubber with a floating date bubble, RAW+JPEG pairs collapsed to one
  cell with a RAW badge.
- **Automatic backup** — content-addressed (BLAKE2b, matches the hub's `hashing.py`),
  resumable chunked uploads, WorkManager content-trigger + hourly safety net, Wi-Fi-only
  and charging-only options. Phone shots route into `Personal Photos/` on the hub.
- **Archive browsing** — the full server library with folder shelves (RAWS, Personal
  Photos, Exported Edits) and semantic search.
- **Unified search** — one query hits both the archive (semantic) and the device (name).
- **Full viewer** — swipe-through pager, pinch/double-tap zoom, swipe-down to dismiss,
  video playback with controls, EXIF + location + backup-state info sheet, share, trash,
  "use as", "open with".
- **Free up space** — trashes device copies the hub has confirmed, past a keep window,
  silently when the Manage Media special access is granted.
- **System integration** — launcher gallery, `ACTION_VIEW` / camera `REVIEW` default
  viewer, `ACTION_PICK` / `GET_CONTENT` picker, share target, multi-select share/trash.
- **Trash** — restore or delete-forever, with the 30-day system retention.
- **First-run onboarding**, themed splash, dark-first Material 3 UI.

## Build

Requires **JDK 17** (Gradle/AGP won't run on the JDK 8 that's often first on PATH):

```powershell
$env:JAVA_HOME = "C:\path\to\jdk-17"
.\gradlew.bat testDebugUnitTest assembleDebug   # unit tests + debug APK
.\gradlew.bat assembleRelease                    # minified, shrunk, signed release APK
```

Debug APK: `app/build/outputs/apk/debug/app-debug.apk`.
Release APK (~11 MB): `app/build/outputs/apk/release/app-release.apk`.

### Release signing

Release signing is opt-in. Set `AZIMUTH_KEYSTORE` and
`AZIMUTH_KEYSTORE_PASSWORD`; otherwise Gradle produces an unsigned release APK.
The keystore lives outside the repository and is never committed:

```powershell
keytool -genkeypair -v -keystore C:\secure\azimuth-release.keystore `
  -alias azimuth -keyalg RSA -keysize 2048 -validity 10000 `
  -dname "CN=Azimuth Photo"
$env:AZIMUTH_KEYSTORE = "C:\secure\azimuth-release.keystore"
$env:AZIMUTH_KEYSTORE_PASSWORD = Read-Host -MaskInput "Keystore password"
```

## Hub endpoints used

The app asks for the user's hub URL during onboarding. Read paths need no auth
by default; sync paths accept an optional `X-Device-Token`.

- `GET /api/rankings?sort=date_taken&limit=&offset=&q=&deep=true&folder=` — library / search.
- `GET /api/thumb/{sm|md|lg}/{id}` — thumbnails.
- `GET /api/folders` — folder tree (shelf chips).
- `GET /api/stats` — library counts (connection test).
- `GET /api/image/{id}/exif` — EXIF for the info sheet.
- `POST /api/sync/manifest`, `GET|POST /api/sync/upload/{hash}[/status]` — backup protocol.

## Architecture

- **`data/`** — `DeviceMedia` (MediaStore queries + RAW-pair collapse), `ArchiveApi`
  (hub library client), `Settings` (DataStore-backed preferences).
- **`backup/`** — `Hashing` (BLAKE2b identity), `SyncClient` (manifest + resumable
  upload), `BackupWorker` + `BackupScheduler` (WorkManager), `BackupDb` (per-item state),
  `FreeUpSpace` (aged cleanup).
- **`ui/`** — `TimelineScreen` / `MediaGrid` (device grid), `ArchiveScreen` (library),
  `SearchScreen`, `ViewerScreen` (full-screen pager), `TrashScreen`, `SettingsScreen`,
  `OnboardingScreen`, `FastScrollScrubber`, `Theme`.
- **Activities** — `MainActivity` (tabs), `ViewerActivity` (VIEW/REVIEW), `PickerActivity`
  (PICK/GET_CONTENT), `ShareActivity` (share target). `App` holds the Coil image loader and
  notification channel.

Package `app.azimuthphoto.mobile`, minSdk 33, targetSdk 35, Kotlin + Compose (Material 3).
