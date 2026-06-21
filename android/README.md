# photoArchive Android

A self-hosted Android companion for photoArchive.

The app talks directly to your Omarchy photoArchive server. It does not use
Google services, cloud sync, Firebase, or a hosted account layer. Tailscale is
the intended private network path.

## First Slice

- Browse the archive in a phone-native photo grid.
- Search the archive through the existing local photoArchive search API.
- Open a full-screen preview.
- Import selected Android photos into Omarchy through the photoArchive import API.
- Store only the server URL on the phone.

## Default Server

The default server URL is:

```text
http://omarchy.tail0eeded.ts.net:8000
```

You can change it from the app's Server button. Automated builds can also set
the default with `PHOTOARCHIVE_ANDROID_SERVER_URL` or Gradle property
`photoArchiveDefaultServerUrl`.

## Build

From the repo root on Omarchy:

```bash
./scripts/photoarchive-android-build
```

The debug APK is written to:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

## End-to-End Gate

From the repo root on Omarchy:

```bash
./scripts/photoarchive-android-e2e
```

This verifies the photoArchive server over Tailscale, runs a no-phone Android
smoke test against the live local server, builds the APK against the verified
URL, and copies the result to `outputs/photoarchive-android-debug.apk`.

If `adb` and an authorized Android device or working emulator are available, it
also installs, launches, captures a screenshot/UI dump/logcat, and fails if the
app is offline.

Use the hard gate before handing the APK to someone:

```bash
./scripts/photoarchive-android-e2e --require-device
```

## Release

For the normal invisible loop, run:

```bash
./scripts/photoarchive-android-release
```

It runs the end-to-end gate, rebuilds the APK, verifies the public download URL
serves that exact file, and prints the phone link plus hash.
