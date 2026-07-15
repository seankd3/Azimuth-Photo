# AUTH_SPEC v1 — Owner authentication (frozen 2026-07-15)

Closes the three open criticals from the hardening audit: owner APIs unauthenticated,
LAN pairing-code minting, and the API-writable `publish_hook` RCE. One design, no accounts,
no cloud, share visitors untouched.

## Product shape

There is exactly one human: **the owner**. Everything they can do is protected by one
credential — the **owner key** — created at first run and never asked for again on
trusted surfaces.

- **First run**: the setup wizard generates a random owner key (32 url-safe chars,
  `secrets.token_urlsafe(24)`), shows it ONCE with copy button + "store this somewhere
  safe", and stores only its scrypt hash (reuse `features/share/auth.py` scrypt format).
  The wizard may instead accept a user-chosen passphrase (min 8 chars). Existing installs
  (already-completed setup): auth stays OFF until a key is set via
  `Settings → Security → Set owner key` or the `--set-owner-key` CLI path — but the app
  shows a persistent, dismissable "Unsecured" banner in the System drawer until then.
- **Browser**: unauthenticated browser request to any protected page → minimal
  Azimuth-branded unlock page (mirror the share-unlock page pattern): one passphrase
  field, submits to `POST /api/auth/unlock`, sets a signed HMAC session cookie
  (`pa_o`, pattern of share cookies, 90-day max age, SameSite=Lax, HttpOnly). Wrong
  attempts use the same throttling budget as share unlock.
- **API clients** (satellite, scripts, curl): `Authorization: Bearer <owner key>`.
- **Paired devices ARE the owner**: a valid paired-device token (existing
  `features/sync/device_auth`) authenticates every owner route, not just sync routes.
  Android and satellites keep working with zero client change. Chain of trust holds
  because *minting* a pairing code now requires owner auth.
- **Loopback is exempt**: requests whose client address is loopback bypass owner auth
  (the person at the server's own keyboard is the owner; Tauri shell keeps working; this
  is also the recovery path if the key is lost — visit from the server machine, set a
  new key). Only true loopback (`ip.is_loopback`), never LAN/tailnet.
- **Rotation/recovery**: `Settings → Security` (owner-authed or loopback) can rotate the
  key; rotating invalidates all browser sessions (bump a per-install session epoch mixed
  into the cookie HMAC) but NOT paired-device tokens.

## Enforcement — default-deny middleware

New `web/core/owner_auth.py` middleware registered next to the origin guard. It protects
**everything** — every page, every `/api/*` route, GET included — except an explicit
public allowlist:

- `/share/*` + the API routes share pages call (exactly the set the share feature
  documents as public; enumerate them, don't glob `/api/share*` blindly)
- published client-gallery public routes
- `/api/auth/unlock`, the unlock page itself, and static assets (`/static/*`) — static
  serves only css/js/img, verify nothing sensitive is mounted there
- `/api/setup/*` and the wizard page ONLY while setup is incomplete (fresh install has
  no data); after `setup/complete` they flip to protected
- health endpoint if one exists, `/api/version` may stay public (harmless)
- sync routes already authenticated by device tokens keep their existing check (they are
  "protected" via device identity; do not double-lock them behind browser cookies)

Auth passes if ANY of: valid `pa_o` cookie · valid owner bearer · valid device token ·
loopback client · no owner key configured yet (legacy install, pre-key). Page routes get
a redirect to the unlock page; API routes get 401 JSON. The middleware must see the real
client IP the same way `browser_origin._is_trusted_proxy` does — do not trust
`X-Forwarded-For` from non-loopback peers.

## publish_hook — remove the RCE class entirely

Owner auth alone is not enough for a setting that executes shell. Remove `publish_hook`
from API-writable settings altogether: it becomes server-side-only configuration
(`PHOTOARCHIVE_PUBLISH_HOOK` env var or the settings file edited on disk). The settings
API returns it read-only (masked) and rejects writes with a clear message. Deployer reads
the env/file value. Document in docs/data-and-privacy.md.

## Pairing

`POST` routes that mint or list pairing codes/devices require owner auth (middleware
covers them; add explicit tests). Redeeming a code stays public by necessity — codes are
short-lived, single-use, rate-limited; verify all three properties and fix if not true.

## Out of scope (v1)

Multi-user, roles, OAuth, per-share owner identity, TLS. No new dependencies.

## Acceptance

- Unauthenticated LAN caller: can load share links and the unlock page, NOTHING else —
  add a sweep test that walks every registered route (introspect the FastAPI app) and
  asserts each is either on the documented public allowlist or returns 401/redirect
  without credentials. This test is the spec.
- Owner cookie, bearer, device token, and loopback each independently unlock a protected
  route (4 tests).
- publish_hook: API write rejected; env-configured hook still fires in the deployer test.
- Pairing mint requires auth; redeem is single-use + expiring (tests).
- Wizard flow sets a key on fresh install; legacy install boots unlocked with the
  "Unsecured" banner state exposed by `/api/auth/status`.
- Full smoke suite green (existing tests may need a test-mode fixture that sets a known
  owner key or marks loopback — prefer making TestClient requests count as loopback via
  the test client address, which they are).
