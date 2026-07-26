# Linux service deployment

These files are portable templates, not a maintainer machine snapshot.
Docker Compose remains the easiest installation path; use systemd when the
server should run directly on Linux.

## 1. Prepare the service account and checkout

```bash
sudo useradd --system --home-dir /var/lib/azimuth-photo --create-home azimuth
sudo git clone https://github.com/Sean-Kenneth-Doherty/azimuth-photo.git /opt/azimuth-photo
sudo python3 -m venv /opt/azimuth-photo/.venv
sudo /opt/azimuth-photo/.venv/bin/pip install -r /opt/azimuth-photo/web/requirements.txt
sudo chown -R azimuth:azimuth /opt/azimuth-photo /var/lib/azimuth-photo
sudo install -d -o azimuth -g azimuth /var/cache/azimuth-photo
```

Grant the `azimuth` account read/write access to the selected photo, intake,
and backup directories. Do not make the source checkout a runtime-data folder.

## 2. Configure local storage and networking

```bash
sudo install -d -m 0750 /etc/azimuth-photo
sudo cp deploy/azimuth-photo.env.example /etc/azimuth-photo/azimuth-photo.env
sudo editor /etc/azimuth-photo/azimuth-photo.env
```

The tracked example uses neutral paths. Put installation-specific host
addresses, mount points, cache budgets, and photo roots only in the copied
environment file.

## 3. Install and verify

```bash
sudo cp deploy/azimuth-photo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now azimuth-photo.service
systemctl status azimuth-photo.service
curl --fail http://127.0.0.1:8000/api/dev/status
```

If originals are on a slow HDD or NAS, keep the catalog and active caches under
`/var/lib` and `/var/cache` on SSD, and leave
`AZIMUTH_BULK_HDD_CONCURRENCY=1`.

The restore-drill unit and timer are optional. Read
[RESTORE_DRILL.md](RESTORE_DRILL.md) before enabling them.
