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
   AZIMUTH_SMOKE_MODE=1 .venv/bin/python -m pytest -q
   ```

3. Commit the release preparation, then create and push an annotated tag:

   ```bash
   git commit -am "Release Azimuth Photo 0.1.0"
   git tag -a v0.1.0 -m "Azimuth Photo 0.1.0"
   git push origin main --follow-tags
   ```

The tag triggers the release workflow. It builds and pushes
`ghcr.io/<owner>/azimuth-photo:v0.1.0` plus `:latest`, builds the Linux frozen
server with `scripts/build_server.py`, attaches
`azimuth-photo-server-v0.1.0.tar.gz`, and asks GitHub Releases to generate
notes from commits since the previous release tag.

The shipped `docker-compose.yml` builds the image locally (`build: .` with the
local tag `azimuth-photo:latest`), so `docker compose pull` does not fetch
releases. Compose users update with:

```bash
git pull
docker compose up -d --build
```

Or, to run the published registry image directly, pull the release tag then
recreate the container:

```bash
docker pull ghcr.io/<owner>/azimuth-photo:v0.1.0
```

## Desktop signing and updates

The Tauri updater is configured to read the release manifest at:

`https://github.com/Sean-Kenneth-Doherty/azimuth-photo/releases/latest/download/latest.json`

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

Every server exposes `GET /api/version`. It answers with the wire contract that
server speaks, and with the identity the desktop auto-updater reads:

```json
{
  "app_version": "1.0.0-rc.1",
  "api_rev": 2,
  "capabilities": ["sync.catalog_v2", "thumbs.trash_readthrough", "trash.scoped_empty"],
  "sha": "<running git sha>",
  "bundle_sha256": "",
  "schema_version": 31
}
```

`api_rev` in `web/core/version.py` is the compatibility number, not
`app_version`. Increase it when an existing cross-node request or response
changes meaning; a release on its own does not change compatibility. The last
three fields belong to
[`CLIENT_AUTOUPDATE_SPEC.md`](CLIENT_AUTOUPDATE_SPEC.md).

A paired satellite probes that endpoint at most once every ten minutes and
keeps the answer in `web/features/sync/contract.py`. `/api/sync/status` reports
it as `hub_health`:

| `hub_health` | Cause |
|---|---|
| `ok` | The hub's `api_rev` is not below the satellite's. A server with no hub of its own also reports `ok`. |
| `needs_update` | The hub answered with a lower `api_rev`, or with no `api_rev` at all. A malformed or missing version response reads the same way, so an unknown server is never treated as compatible. |
| `unreachable` | The probe failed. |
| `not_connected` | The library holds mirrored hub photos and no hub is attached. |

The desktop sync chip is the surface for these states, and the only one. On
`needs_update` it shows `!` in place of its arrow and says "Some actions are
paused until the hub updates."

For a rolling upgrade, update the hub first. Wait until it restarts and
completes its schema migration, then update the satellites. A satellite that is
updated first continues to sync where it can, and shows that calm notice until
the hub catches up.
