# Azimuth Photo — release marathon spec

You are working in the `android/` Gradle project (Kotlin + Compose, package `app.azimuthphoto.mobile`, minSdk 33, targetSdk 35). This spec turns the working app into a release-ready Google Photos replacement. The design decisions below are FROZEN — implement them, do not redesign. Existing visual language: dark-first (`ui/Theme.kt`: Ink/Panel/PanelHigh/TextPrimary/TextSecondary/Accent), 4-col grid with 2dp gaps, GPhotos-informed but calmer.

Work batch by batch, in order. After each batch: run
`JAVA_HOME=C:\Users\smast\AppData\Local\Programs\Microsoft\jdk-17.0.10.7-hotspot` then `.\gradlew.bat testDebugUnitTest assembleDebug` — it must pass — then `git add` the touched files and `git commit` on this branch (android-app) with message `android: <batch letter> — <summary>`. Never commit a red build. Do not push. Do not touch files outside `android/`.

Hub API reference (server at http://100.102.150.104:8000, all read paths take no auth; sync paths accept optional X-Device-Token):
- `GET /api/rankings?sort=date_taken&limit=&offset=&q=&deep=true&folder=/abs/path` → `{images:[{id, filename, aspect_ratio, date_taken, camera_model, lens, file_ext, file_size, width, height, thumb_url, date_group, latitude, longitude, flag, elo}], visible_images, total_images}`; also `sort=elo`.
- `GET /api/thumb/{sm|md|lg}/{id}` JPEG.
- `GET /api/folders` → `{folders:[{path,count,depth}]}`.
- `GET /api/stats` → library stats (inspect the JSON shape at runtime; render defensively).
- `GET /api/image/{id}/exif` → EXIF dict.
- Existing client: `data/ArchiveApi.kt`; sync client: `backup/SyncClient.kt`.

## Batch A — Native intents: behave like the system's photo app

A1. **Camera review flow.** ViewerActivity gains intent filters for `com.android.camera.action.REVIEW` and `android.provider.action.REVIEW` (+ `REVIEW_SECURE`, exported, `showWhenLocked|turnScreenOn` for the secure variant) with `data android:mimeType="image/*"` and `video/*`. Behavior: resolve the incoming content URI to a MediaStore id; load the device timeline (reuse `DeviceMedia.queryAll` + the RAW-pair collapse logic — extract that collapse into `DeviceMedia.collapseRawPairs(items)` so TimelineScreen and ViewerActivity share it); open the full ViewerScreen pager positioned at that item so the user can swipe from the camera straight through their roll. Fall back to single-URI view if the id can't be resolved.

A2. **System picker.** New `PickerActivity` handling `ACTION_PICK` and `ACTION_GET_CONTENT` for `image/*` and `video/*` (+ `EXTRA_ALLOW_MULTIPLE`). UI: the timeline grid (reuse the grid/row composables — factor the day-grouped grid body out of TimelineScreen into a reusable `MediaGrid` composable taking (items, selectedIds, onTap, onLongPress)) with a top bar "Select photo" / "Select items" + confirm check when multiple. Result: `setResult(RESULT_OK)` with data URI (single) or ClipData (multiple), `FLAG_GRANT_READ_URI_PERMISSION`.

A3. **Multi-select in the timeline.** Long-press enters selection mode: cells get a check circle top-left (filled Accent when selected), tap toggles, top app bar swaps to count + actions: Share (ACTION_SEND_MULTIPLE chooser with content URIs), Trash (single `MediaStore.createTrashRequest` for all selected), Backup now (BackupScheduler.runNow). Back or ✕ exits selection. Keep it buttery: selection state is a `Set<Long>` in a rememberSaveable.

A4. **"Use as" (set wallpaper/contact photo).** Viewer top bar overflow menu (⋮) with "Use as" → `Intent(ACTION_ATTACH_DATA)` chooser, and "Open with" → ACTION_VIEW chooser excluding self.

A5. **Share FROM other apps already works (ShareActivity)**; extend it: after triggering runNow, show a proper themed mini-Activity (not just a Toast): small centered card "Backing up N items to your archive" that auto-finishes after 1.5s.

## Batch B — Grid feel: density, scrubber, viewer polish

B1. **Pinch grid density.** Timeline grid supports 3/4/5 columns via pinch (detectTransformGestures on the grid container; thresholded zoom accumulates → step column count, animate with `animateDpAsState` on cell padding is unnecessary — just change GridCells.Fixed count; Compose animates item placement with `Modifier.animateItem()` on cells). Persist chosen density in SettingsStore.

B2. **Fast-scroll scrubber.** Right-edge drag handle appears while scrolling (fade in/out): vertical drag maps to grid scroll proportionally; while dragging show a floating date bubble (month + year of the first visible item, e.g. "Jul 2026"). Implement against `LazyGridState` (`layoutInfo.totalItemsCount`, `scrollToItem`). Reuse for the Archive grid too (date from `date_group`).

B3. **Viewer dismissal + zoom polish.** ViewerScreen: swipe-down to dismiss (vertical drag when not zoomed: translate + scale the image toward 0.8 and fade a black backdrop, release past threshold → onClose, else spring back). Double-tap zooms toward the tap point (not center) — adjust offsetX/offsetY relative to tap position. When zoomed, single finger pans (currently requires two — make pan work with one pointer while scale > 1 via detectDragGestures gated on scale).

B4. **Video controls.** VideoPage: `useController = true`, controller auto-hide 2s, keep screen on while playing (`Modifier` + `LocalView.current.keepScreenOn`), mute toggle button overlay bottom-right, release players for pages > 1 away from current (pager `beyondViewportPageCount = 0` and stop non-active players fully).

B5. **Info sheet upgrade.** Add: camera model + aperture/shutter/ISO/focal from EXIF (androidx.exifinterface on the content URI — already a dependency), a "Located at lat, lon" row with an "Open in Maps" action (`geo:` intent) when ACCESS_MEDIA_LOCATION grants coordinates, and the backup state row ("Backed up to archive" with the hub image id, or "Not backed up yet") pulled from BackupDb.

## Batch C — Trash, empty states, and the Photos tab top bar

C1. **Trash screen.** Reachable from Settings ("Trash") and from the Photos tab overflow. Query MediaStore with `MediaStore.QUERY_ARG_MATCH_TRASHED = MATCH_ONLY` (Files collection), same grid, per-item and multi-select Restore (`createTrashRequest(..., false)`) and Delete forever (`createDeleteRequest`) — both fire the system flow; with MANAGE_MEDIA they're silent. Show "Items are removed forever after 30 days" caption.

C2. **Photos tab top bar.** Small translucent top bar over the grid: app wordmark "Azimuth" left (Text, titleLarge, TextPrimary), right: backup status glyph — cloud-check when idle+all safe, animated cloud-arrow while BackupWorker.progress.running with "n/total" mini-label, cloud-off when backup disabled — tapping it opens Settings. Bar background: Ink at 85% alpha, sits above the grid (grid content padds under it).

C3. **Empty & error states, everywhere.** Timeline empty (no media): centered friendly copy "No photos yet — take one and it'll land here (and in your archive)". Archive offline: banner card at top "Archive unreachable — check Tailscale" with Retry button, keep last-loaded grid if any. Archive empty query: "Nothing matches ‘q’". Settings shows server reachability line (ping /api/stats with 3s timeout when Settings opens: "Connected — 145,201 photos" or "Unreachable").

## Batch D — Search tab (the GPhotos magic)

D1. Replace the 3-tab bar with 4: Photos / Search / Archive / Settings (icons: Photo, Search, Cloud, Settings — rounded when selected, outlined otherwise, same as current pattern).

D2. **SearchScreen.** Top: the existing rounded search field (autofocus off). Below, before any query: shelf rows — "Your shelves" (the ArchiveApi.shelves() chips as tappable cards with count), and "On this device" chips (buckets from DeviceMedia.queryBuckets: Camera, Screenshots, etc.). On submit: two sections, rendered as one LazyVerticalGrid with full-span headers — "From your archive" (ArchiveApi.page(q=query, deep=true), tap → ArchiveViewer) and "On this device" (local filename contains-match, case-insensitive, over queryAll + collapseRawPairs; tap → ViewerScreen). Keep both lazily paged (archive) / capped at 200 (local). Recent searches (last 8, DataStore) as chips under the field before results; tap re-runs.

D3. Move the search field OUT of ArchiveScreen (Archive keeps only shelf chips + grid; its search now lives in the Search tab).

## Batch E — Release readiness

E1. **Onboarding (first run).** If media permission not granted OR server never configured: a 3-page horizontal pager: (1) wordmark + "Your photos. Your server. Nobody else." + Continue; (2) permission rationale → system prompt (images+video+location+notifications); (3) server card: URL field prefilled with default, "Test connection" button hitting /api/stats showing ✓ photo-count or ✗ error, backup toggle (default on), Done. Store `onboarded=true` in SettingsStore; MainActivity routes to onboarding until true. Keep it minimal and elegant — no illustrations, generous whitespace, the aperture mark from ic_launcher_fg as the only graphic.

E2. **Release build.** In app/build.gradle.kts release block: `isMinifyEnabled = true`, `isShrinkResources = true`; write proguard-rules.pro keeps for kotlinx-serialization (@Serializable classes: keep `**$$serializer`, `Companion` fields per kotlinx docs), OkHttp/Okio (standard -dontwarn), BouncyCastle, media3. Generate a keystore OUTSIDE the repo at `C:\Users\smast\.azimuth\release.keystore` (keytool, RSA 2048, validity 10000, alias azimuth, store+key password `azimuth-local` — it's a personal app; print the exact keytool command you ran) and wire a `signingConfigs.release` reading it via `System.getenv("AZIMUTH_KEYSTORE_PASSWORD") ?: "azimuth-local"`. Acceptance for this item additionally: `.\gradlew.bat assembleRelease` succeeds and the APK installs (`adb install` if a device is attached; otherwise note it).
E3. **Themed icon + splash.** Add `androidx.core:core-splashscreen`, Theme.PhotoArchive.Splash with Ink background + the aperture vector, postSplashScreenTheme → current theme. Manifest application uses splash theme on MainActivity. The adaptive icon already has a monochrome layer — verify and keep.
E4. **Version + naming.** versionCode 3, versionName "1.0". `android:label` stays "Azimuth Photo" (full name for launcher; it fits).
E5. **Predictive back.** Already `enableOnBackInvokedCallback=true`; ensure every BackHandler usage still works with gesture nav (viewer, selection mode, trash, search clearing) — selection mode must consume back before tab navigation.
E6. **Battery/perf.** Coil: single ImageLoader in App with memoryCache 25% and diskCache 512MB, crossfade false for grid, respectCacheHeaders false for hub thumbs (they're immutable). BackupWorker: BackoffPolicy.EXPONENTIAL 30s on the one-time requests. Add "Only while charging" backup toggle (Settings + Constraints.setRequiresCharging).
E7. **Tests.** Unit tests: RAW-pair collapse (pairs collapse, solo DNG stays, TS pattern), shelf chain-collapse (fixture folder list → expected shelves incl. date-stop rule), worker failure classification (HubHttpException 422 permanent vs IOException transient). Keep them JVM-only.

## Batch F — Final sweep

F1. Grep the module for leftover TODOs, unused imports, and dead code you introduced; clean them.
F2. Write `android/README.md`: what the app is, features, build instructions (JDK17 path, gradlew commands), release signing note, hub endpoints used, architecture map (one paragraph per package).
F3. Final full run: `.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease` all green; final commit.

## Global rules
- No new heavyweight dependencies beyond those named (core-splashscreen is the only new one).
- Every user-visible string: sentence case, no exclamation marks, calm tone.
- Nothing blocks the main thread: all MediaStore/DB/network on Dispatchers.IO.
- Match existing code style: modular single-purpose files, minimal comments (constraints only), trailing commas as seen.
- If a spec item conflicts with reality (API missing, etc.), implement the closest faithful version and note the deviation in the commit message — do not stall.
