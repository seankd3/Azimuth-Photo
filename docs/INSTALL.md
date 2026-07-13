# Install photoArchive

One always-on library server. Point it at your photos, open the web page.

## Docker (recommended on a NAS)

### Docker Compose

```bash
git clone https://github.com/Sean-Kenneth-Doherty/azimuth-photo.git
cd photo-archive
mkdir -p photos data
# Put originals in ./photos (or edit docker-compose.yml to mount your real folder)
docker compose up -d --build
```

Open `http://<that-machine>:8000`.

- **Photos** live in the folder you mount at `/photos` (your originals stay yours).
- **Catalog + caches + settings** live under `/data` (`PHOTOARCHIVE_HOME`).

### Synology Container Manager

1. Container Manager → Project → Create from `docker-compose.yml`.
2. Map your photo share to `/photos`, and a folder for app data to `/data`.
3. Publish port `8000`.
4. Open `http://<nas>:8000`.

### Unraid

Import `deploy/unraid-template.xml` (or add the image manually):

| Path / port | Maps to |
|---|---|
| Host photo share | `/photos` |
| `appdata/photoarchive` | `/data` |
| Host `8000` | Container `8000` |

### TrueNAS

Use a custom app / Compose with the same two mounts (`/photos`, `/data`) and port `8000`. Same image as above.

### Phone discovery on the LAN

photoArchive can announce itself on the local network. That needs **host networking** (not bridge). In Compose, set `network_mode: host` and drop the `ports:` section — see comments in `docker-compose.yml`. On Synology/Unraid/TrueNAS, pick host network if you want “Found on your network” from other devices. Tailscale still works either way.

---

## Bare binary (no Docker)

Build on the machine (or copy the folder from a matching OS/CPU):

```bash
python3.12 scripts/build_server.py
./dist/photoarchive-server/photoarchive-server
```

The builder uses a separate venv (default `~/.cache/photoarchive-pkg-venv`) and
never touches `web/.venv`. Heavy build scratch can go on
`/mnt/expansion/tmp/pkg-pyinstaller` via `PHOTOARCHIVE_BUILD_WORK`.

Opens on `http://127.0.0.1:8000`. Useful env vars:

| Variable | Meaning |
|---|---|
| `PHOTOARCHIVE_HOST` | Bind address (default `127.0.0.1`; use `0.0.0.0` on a NAS) |
| `PHOTOARCHIVE_PORT` | Port (default `8000`) |
| `PHOTOARCHIVE_HOME` | One folder for catalog, caches, settings |
| `PHOTOARCHIVE_MODE` | `hub` (always-on library) is the usual server choice |

The Windows build of this folder is what the desktop app can ship as its sidecar.

---

## Where data lives

| What | Docker | Bare binary (Linux, no overrides) |
|---|---|---|
| Originals | Your `/photos` mount | Wherever you add as a source folder |
| Catalog DB | `/data/data/catalog/` | `~/.local/share/photoarchive/` |
| Settings | `/data/config/` | `~/.config/photoarchive/` |
| Previews / caches | `/data/cache/` | `~/.cache/photoarchive/` |

Existing developer checkouts that already have `web/photoarchive.db` keep using that in-repo layout. New installs never move your photos.

---

## First five minutes

1. Open the web UI.
2. Add your photos folder (in Docker that is often `/photos`).
3. Let the scan run — the grid fills as it goes.
4. Optional later: AI features need extra packages or the AI image docs; the base install stays light on purpose.

More detail: [getting-started.md](getting-started.md), [DISTRIBUTION_SPEC.md](DISTRIBUTION_SPEC.md).
