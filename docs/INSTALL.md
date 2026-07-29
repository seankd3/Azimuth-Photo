# Install Azimuth Photo

One always-on library server. Point it at your photos, open the web page.

## Docker (recommended on a NAS)

### Docker Compose

```bash
git clone https://github.com/Sean-Kenneth-Doherty/azimuth-photo.git
cd azimuth-photo
mkdir -p photos data
# Put originals in ./photos (or edit docker-compose.yml to mount your real folder)
docker compose up -d --build
```

Open `http://<that-machine>:8000`.

- **Photos** live in the folder you mount at `/photos` (your originals stay yours).
- **Catalog + caches + settings** live under `/data` (`AZIMUTH_HOME`).

### Synology Container Manager

1. Container Manager → Project → Create from `docker-compose.yml`.
2. Map your photo share to `/photos`, and a folder for app data to `/data`.
3. Publish port `8000`.
4. Open `http://<nas>:8000`.

### Unraid

There is no published registry image yet — build `azimuth-photo:latest` on the
server first (`docker compose build` in this repo, or `docker load` a saved
image). Then import `deploy/unraid-template.xml` (or add the image manually):

| Path / port | Maps to |
|---|---|
| Host photo share | `/photos` |
| `appdata/azimuth` | `/data` |
| Host `8000` | Container `8000` |

### TrueNAS

Use a custom app / Compose with the same two mounts (`/photos`, `/data`) and port `8000`. Same image as above.

### Phone discovery on the LAN

Azimuth Photo can announce itself on the local network. That needs **host networking** (not bridge). In Compose, set `network_mode: host` and drop the `ports:` section — see comments in `docker-compose.yml`. On Synology/Unraid/TrueNAS, pick host network if you want “Found on your network” from other devices. Tailscale still works either way.

---

## Bare binary (no Docker)

Build on the machine (or copy the folder from a matching OS/CPU):

```bash
python3.12 scripts/build_server.py
./dist/azimuth-server/azimuth-server
```

The builder uses a separate venv (default `~/.cache/azimuth-pkg-venv`) and
never touches `web/.venv`. Build scratch uses the platform temporary directory;
override it via `AZIMUTH_BUILD_WORK`.

Opens on `http://127.0.0.1:8000`. Useful env vars:

| Variable | Meaning |
|---|---|
| `AZIMUTH_HOST` | Bind address (default `127.0.0.1`; use `0.0.0.0` on a NAS) |
| `AZIMUTH_PORT` | Port (default `8000`) |
| `AZIMUTH_HOME` | One folder for catalog, caches, settings |
| `AZIMUTH_MODE` | `hub` (always-on library) is the usual server choice |

The Windows build of this folder is what the desktop app can ship as its sidecar.

---

## Where data lives

| What | Docker | Bare binary (Linux, no overrides) |
|---|---|---|
| Originals | Your `/photos` mount | Wherever you add as a source folder |
| Catalog DB | `/data/data/catalog/` | `~/.local/share/azimuth-photo/` |
| Settings | `/data/config/` | `~/.config/azimuth-photo/` |
| Previews / caches | `/data/cache/` | `~/.cache/azimuth-photo/` |

A source checkout is never used as runtime storage: older developer installs that kept `web/azimuth.db` inside the repo must set `AZIMUTH_DB_PATH` (or `AZIMUTH_HOME`) to that data — or move it into the paths above — to keep their catalog. Azimuth Photo never moves your photos.

---

## First five minutes

1. Open the web UI.
2. Add your photos folder (in Docker that is often `/photos`).
3. Let the scan run — the grid fills as it goes.
4. Optional later: AI features need extra packages or the AI image docs; the base install stays light on purpose.

More detail: [getting-started.md](getting-started.md), [DISTRIBUTION_SPEC.md](DISTRIBUTION_SPEC.md).
For a direct Linux service, see [deploy/README.md](../deploy/README.md).
