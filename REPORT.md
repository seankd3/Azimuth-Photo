# Android UX fix report

Branch: `ux2-android`

1. Complete — collection sharing no longer publishes a private collection without confirmation. Commit `52140b3e`.
   Manual QA: Open a private collection, tap Share, confirm Cancel leaves it private; confirm Publish & share creates and opens a link. Tap Share on a published collection and confirm no publish prompt appears.

2. Complete — saved grid density and pinch adjustment apply to timeline, device, search, collection, person, tag, and similar-photo grids. Commit `435a1e6f`.
   Manual QA: Pinch a grid to change density, navigate through each listed grid, and confirm the selected density remains in use.

3. Complete — collection add, person rename, and person hide feedback now follows the hub response. Commit `7905f43a`.
   Manual QA: Disconnect the hub and attempt each action; confirm the failure toast appears and Add to collection / Rename remains open. Reconnect and confirm the success toast and completion behavior.

4. Complete — archive photos opened from Search offer Add to collection and Find similar. Commit `ce664778`.
   Manual QA: Search for an archive photo, open it, use the overflow menu, add it to a collection, then open Find similar and return to Search.

5. Complete — backup foreground notifications show progress, open the app when tapped, and post a failure summary. Commit `0b48cb65`.
   Manual QA: Start a backup with pending media; verify the notification progress and tap-through. Make an upload fail and verify the completion notification reports the failed count.

6. Complete — Library home, Places, Collection, and Memories distinguish hub errors from empty results and provide Retry. Commit `882baa48`.
   Manual QA: Disable hub connectivity, open each surface, confirm Archive offline and Retry appear; restore connectivity and confirm Retry loads the content or true empty state.

7. Complete — timeline, Library, and Search keep their grid composed underneath the viewer overlay. Commit `0ddbe53e`.
   Manual QA: Scroll each grid to a nonzero position, open and close a photo, and verify the same items remain at the same scroll position.

8. Complete — timeline refreshes after debounced MediaStore image/video changes and on app resume. Commit `43de684b`.
   Manual QA: Add or remove a photo while the app is visible and verify the timeline refreshes after a short delay; background the app, change media, resume, and verify it refreshes.

Verification: with `ANDROID_HOME` and `ANDROID_SDK_ROOT` set to `/home/sean/Android/Sdk`, `bash ./gradlew --console=plain :app:compileDebugKotlin` and `bash ./gradlew --console=plain :app:assembleDebug` both completed successfully. The local verification temporarily replaced the Windows-only release-keystore path in `android/app/build.gradle.kts`; that path was restored before this report.
