/**
 * DISTRIBUTION remote-access Playwright proof on probe :8133.
 *
 * Usage:
 *   NODE_PATH=.../node_modules node scripts/remote-access-playwright-proof.cjs [baseUrl]
 *
 * Expects PHOTOARCHIVE_TS_DRYRUN=1 on the server. Screenshots → /tmp/dist1/remote-*.png
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = (process.argv[2] || process.env.PHOTOARCHIVE_PROBE_URL || 'http://127.0.0.1:8133').replace(/\/$/, '');
const OUT = '/tmp/dist1';
fs.mkdirSync(OUT, { recursive: true });

function shot(name) {
  return path.join(OUT, `remote-${name}.png`);
}

async function openDrawer(page) {
  await page.locator('#system-btn').click({ timeout: 10000 });
  await page.waitForSelector('#drawer.on', { timeout: 10000 });
  await page.waitForSelector('#drawer-body .dr-sec', { timeout: 15000 });
}

async function closeDrawer(page) {
  const close = page.locator('#drawer-close');
  if (await close.count()) {
    await close.click().catch(() => {});
  } else {
    await page.locator('#drawer-scrim').click({ force: true }).catch(() => {});
  }
  await page.waitForTimeout(300);
}

function basePayload(state, extras = {}) {
  const dns = 'photoarchive.example.ts.net';
  const https = `https://${dns}:8443`;
  return {
    access_mode: 'local',
    current_url: BASE,
    hub_mode: true,
    mode: 'hub',
    tailscale: {
      state,
      available: state !== 'absent',
      ip: state === 'up' ? '100.64.0.1' : '',
      dns_name: state === 'up' ? dns : '',
      install_url: 'https://tailscale.com/download',
      up_command: 'tailscale up',
      serve_command: 'sudo tailscale serve --bg --https=8443 http://127.0.0.1:8133',
      https_url: state === 'up' ? https : '',
      url: state === 'up' ? `http://${dns}:8133` : '',
      dry_run: true,
      error:
        state === 'absent'
          ? 'Tailscale is not installed'
          : state === 'logged-out'
            ? 'Tailscale is installed but not logged in'
            : '',
      ...extras,
    },
  };
}

async function withRemoteMock(page, payload, fn) {
  await page.unroute('**/api/remote-access').catch(() => {});
  await page.route('**/api/remote-access', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
  try {
    return await fn();
  } finally {
    await page.unroute('**/api/remote-access').catch(() => {});
  }
}

async function captureState(page, name, payload) {
  return withRemoteMock(page, payload, async () => {
    await page.goto(`${BASE}/d`, { waitUntil: 'networkidle', timeout: 60000 });
    await openDrawer(page);
    const section = page.locator('[data-remote-hub="1"]');
    await section.waitFor({ timeout: 15000 });
    await section.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const file = shot(name);
    await page.screenshot({ path: file, fullPage: false });
    await closeDrawer(page);
    return file;
  });
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const report = { base: BASE, screenshots: [], serve: null };

  try {
    report.screenshots.push(await captureState(page, 'absent', basePayload('absent')));
    report.screenshots.push(await captureState(page, 'logged-out', basePayload('logged-out')));
    report.screenshots.push(await captureState(page, 'up-before-apply', basePayload('up')));

    // Live dry-run Apply: server is forced to "up" with PHOTOARCHIVE_TS_DRYRUN=1.
    await page.goto(`${BASE}/d`, { waitUntil: 'networkidle', timeout: 60000 });
    const live = await page.request.get(`${BASE}/api/remote-access`);
    report.live = await live.json();
    await openDrawer(page);
    await page.waitForSelector('#remote-apply-serve', { timeout: 15000 });
    const [serveResponse] = await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/remote-access/serve') && res.request().method() === 'POST'),
      page.locator('#remote-apply-serve').click(),
    ]);
    report.serve = await serveResponse.json();
    await page.waitForSelector('.remote-ready .remote-url, .remote-qr', { timeout: 15000 });
    const ready = page.locator('[data-remote-hub="1"]');
    await ready.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const applied = shot('up-applied');
    await page.screenshot({ path: applied, fullPage: false });
    report.screenshots.push(applied);

    fs.writeFileSync(path.join(OUT, 'remote-proof.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    if (!report.serve || !report.serve.ok || !report.serve.dry_run) {
      throw new Error(`expected dry-run serve ok, got ${JSON.stringify(report.serve)}`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
