# Field HTTPS — Tailscale Serve for PWA / service worker

Goal: phone and laptop field clients open `/m` as an installable app on flaky
links. Service workers and `beforeinstallprompt` require a **secure context**
(HTTPS on the tailnet, or `localhost`). Plain HTTP still works for browsing and
for the localStorage write queue — install + SW simply stay off.

Do **not** reconfigure the public Funnel on `:443` (it serves unrelated
content). Use a **tailnet-only** Serve on `:8443` in front of the app on `:8000`.

## Exact commands (Omarchy)

App listens on the Tailscale IPv4 at `:8000` (systemd
`azimuth-photo.service`).
Front it with HTTPS:

```bash
# Discover this machine's MagicDNS name (example: photos.example-tailnet.ts.net)
tailscale status --json | python -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'

# Tailnet-only HTTPS on :8443 → local Azimuth Photo :8000
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8000
# If the app is bound to the Tailscale IP instead of loopback:
# sudo tailscale serve --bg --https=8443 http://$(tailscale ip -4):8000
```

Verify:

```bash
tailscale serve status
# Expect something like:
# https://photos.<tailnet>.ts.net:8443 (tailnet only)
# |-- / proxy http://100.x.x.x:8000
```

Open on the phone (Tailscale connected):

```text
https://photos.<tailnet>.ts.net:8443/m
```

To tear down only this serve mapping (leave Funnel alone):

```bash
sudo tailscale serve --https=8443 off
```

Optional env on the uvicorn process if you change the Serve port:

```bash
# Default is 8443. Use 443 or empty to omit the port in advertised URLs.
export AZIMUTH_HTTPS_PORT=8443
```

`GET /api/remote-access` then includes `tailscale.https_url` for the mobile
secure-origin banner.

## What changes for the PWA / service worker

| Surface | Plain HTTP (`:8000`) | HTTPS Serve (`:8443`) |
| --- | --- | --- |
| Browse `/m`, thumbs, flags | Works | Works |
| localStorage write queue | Works | Works |
| Service worker register | **No-op** (`isSecureContext` false) | Registers `/sw.js?v=<static_version>` |
| Shell precache + thumb SWR | Inactive | Active |
| Background Sync wake-up | Unavailable | Optional; falls back to `online` + queue |
| Add to Home Screen / install | Banner points at HTTPS URL | Native install prompt |

Cache busting matches the app idiom: CSS/JS use `?v={{ static_version }}`
(mtime stamp). The SW registers as `/sw.js?v=<same stamp>` and derives
`CACHE_VERSION = azimuth-mobile-<stamp>`, so a deploy replaces the shell cache.

## App-side origin adjustments (HTTP stays working)

All mobile fetches use **root-absolute** paths (`/api/...`, `/static/...`,
`/m`). They follow the page origin, so the same build works on:

- `http://127.0.0.1:8132/m` (probe / local)
- `http://photos.<tailnet>.ts.net:8000/m` (plain HTTP on the tailnet)
- `https://photos.<tailnet>.ts.net:8443/m` (Serve HTTPS)

Guards:

1. **SW registration** only when `window.isSecureContext` — otherwise a silent
   no-op (see `web/templates/mobile.html`).
2. **Secure banner** on HTTP links to `tailscale.https_url` from
   `/api/remote-access`, falling back to `https://<hostname>:8443/...`
   (`web/static/js/mobile/https_origin.js`).
3. **Write queue** always uses localStorage; Background Sync is best-effort on
   secure origins only (`write_queue.js` + `sw.js` sync tag `azimuth-write-queue`).

No `base` href rewrite and no hard-coded production hostname in fetch URLs.

## Probe / Playwright notes

Headless Chromium treats `http://127.0.0.1` as a secure context (localhost
exception), so SW registration can be proven on a probe port. Non-localhost
HTTP hosts are not secure contexts — registration must stay a no-op there.
