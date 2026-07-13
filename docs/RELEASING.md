# Releasing Azimuth Photo

`VERSION` at the repository root is the release version source of truth for the
server, release artifact, and API handshake. It must contain one strict SemVer
value such as `0.1.0`, with no leading `v`. The desktop bundle metadata mirrors
it until the signed desktop job is enabled.

## Cut a release

1. Choose the next SemVer version and update `VERSION`.
2. Update the matching desktop bundle version before enabling the signed desktop
   build, then run the normal checks:

   ```bash
   cd web
   PHOTOARCHIVE_SMOKE_MODE=1 .venv/bin/python -m pytest -q
   ```

3. Commit the release preparation, then create and push an annotated tag:

   ```bash
   git commit -am "Release Azimuth Photo 0.1.0"
   git tag -a v0.1.0 -m "Azimuth Photo 0.1.0"
   git push origin main --follow-tags
   ```

The tag triggers the release workflow. It builds and pushes
`ghcr.io/<owner>/photo-archive:v0.1.0` plus `:latest`, builds the Linux frozen
server with `scripts/build_server.py`, attaches
`azimuth-photo-server-v0.1.0.tar.gz`, and asks GitHub Releases to generate
notes from commits since the previous release tag.

Docker users update with:

```bash
docker compose pull
docker compose up -d
```

Or, for a direct image pull, replace the tag then recreate the container:

```bash
docker pull ghcr.io/<owner>/photo-archive:v0.1.0
```

## Desktop signing and updates

The Tauri updater is configured to read the release manifest at:

`https://github.com/Sean-Kenneth-Doherty/photo-archive/releases/latest/download/latest.json`

Before enabling the `signed-windows-desktop` workflow job, generate a Tauri
updater signing keypair outside the repository. Put its public key in
`desktop/src-tauri/tauri.conf.json`'s `plugins.updater.pubkey`, and add these
repository secrets:

- `TAURI_SIGNING_PRIVATE_KEY`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

The future Windows job must build the signed NSIS installer, generate the
signed `latest.json` manifest, and upload both to the release. Never commit a
private signing key.

## Rolling upgrades and paired satellites

Every server exposes `GET /api/version` as:

```json
{"version":"0.1.0","schema_version":27,"mode":"hub"}
```

During each satellite sync/mirror pass, the satellite reads that endpoint. A
hub older than the satellite's `MIN_COMPATIBLE_HUB` version sets
`server_update_available` in `/api/sync/status`; the satellite’s desktop
drawer then quietly says, “Your Azimuth Photo server needs an update.” A
malformed or unavailable version response is marked `server_incompatible` so
newer sync code does not silently treat an unknown server as compatible.

For a rolling upgrade, update the hub first, wait for it to restart and finish
its schema migration, then update satellites. If a satellite is upgraded first,
it continues normal sync where possible and shows the calm update notice until
the hub catches up.
