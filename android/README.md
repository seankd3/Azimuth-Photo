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

You can change it from the app's Server button.

## Build

From the repo root on Omarchy:

```bash
./scripts/photoarchive-android-build
```

The debug APK is written to:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```
