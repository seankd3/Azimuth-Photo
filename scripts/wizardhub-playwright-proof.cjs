/**
 * WIZARDHUB Playwright proof for the first-run server-connect card on :8141.
 *
 * Usage:
 *   TMPDIR=/mnt/expansion/tmp NODE_PATH=.../node_modules \
 *     node scripts/wizardhub-playwright-proof.cjs http://127.0.0.1:8141
 *
 * Screenshots land in /mnt/expansion/tmp/dist2/wizardhub-*.png.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = (process.argv[2] || process.env.PHOTOARCHIVE_PROBE_URL || 'http://127.0.0.1:8141').replace(/\/$/, '');
const OUT = '/mnt/expansion/tmp/dist2';
fs.mkdirSync(OUT, { recursive: true });

function shot(name) {
  return path.join(OUT, `wizardhub-${name}.png`);
}

async function installApiStubs(page) {
  await page.route('**/api/catalog/folder-picker', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ available: false }),
  }));
  await page.route('**/api/catalog/browse**', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ path: '/photos', parent: '/', is_dir: true, readable: true, roots: [], entries: [] }),
  }));
  await page.route('**/api/scan', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ ok: true }),
  }));
  await page.route('**/api/scan/status', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ scanning: true, total_found: 42 }),
  }));
  await page.route('**/api/settings', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ sync: { mode: 'satellite', has_hub: false } }),
  }));
  await page.route('**/api/discover', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ hubs: [{ name: 'Studio Archive', url: 'http://studio.local:8000' }] }),
  }));
  await page.route('**/api/pair/connect', (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ ok: true, has_hub: true }),
  }));
}

async function enterImport(page) {
  await page.goto(`${BASE}/setup`, { waitUntil: 'networkidle', timeout: 60000 });
  await page.locator('[data-next]').click();
  await page.locator('#su-start').click();
  await page.locator('#su-connect-card:not([hidden])').waitFor({ timeout: 10000 });
  await page.locator('#su-connect-hubs button', { hasText: 'Connect' }).waitFor({ timeout: 10000 });
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const context = await browser.newContext({ viewport: { width: 720, height: 960 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const report = { base: BASE, screenshots: [] };

  try {
    await installApiStubs(page);
    await enterImport(page);

    const discovered = shot('discovered');
    await page.screenshot({ path: discovered, fullPage: true });
    report.screenshots.push(discovered);

    await page.locator('#su-connect-manual').click();
    const manual = shot('manual');
    await page.screenshot({ path: manual, fullPage: true });
    report.screenshots.push(manual);

    await page.locator('#su-connect-url').fill('http://studio.local:8000');
    await page.locator('#su-connect-code').fill('ABCD1234');
    await page.locator('#su-connect-submit').click();
    await page.locator('#su-connect-success:not([hidden])').waitFor({ timeout: 10000 });
    const connected = shot('connected');
    await page.screenshot({ path: connected, fullPage: true });
    report.screenshots.push(connected);

    fs.writeFileSync(path.join(OUT, 'wizardhub-proof.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
