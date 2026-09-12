#!/usr/bin/env node
// The UI in a headless browser: build the harness page, open it, ask it
// questions, capture it. The twin of native_proof.py for a machine with no
// desktop (a cloud session, a CI runner): the same probes, the same PROBE
// lines, the harness's fake library instead of a home, and shape and
// behaviour rather than pixels.
//
//   node scripts/harness_proof.mjs <out.png> [--probe a.js ...] [--gap 1.5] [--size 1366x768]
//
// Each probe is JavaScript evaluated in the page, in order with --gap seconds
// between them so one may act and the next may read what happened. A page
// error fails the run. The browser is Playwright's Chromium: the one the
// cloud container ships (AZIMUTH_CHROMIUM names it, and /opt/pw-browsers is
// looked in), else the one `npx playwright install chromium` fetched once.
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PAGE = path.join(ROOT, 'build', 'harness.html');

function parse(argv) {
  const args = { out: '', probes: [], gap: 1.5, size: [1366, 768] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--probe') args.probes.push(argv[++i]);
    else if (arg === '--gap') args.gap = Number(argv[++i]);
    else if (arg === '--size') args.size = argv[++i].split('x').map(Number);
    else if (!args.out) args.out = arg;
    else throw new Error(`unexpected argument ${arg}`);
  }
  if (!args.out) throw new Error('usage: harness_proof.mjs <out.png> [--probe a.js ...] [--gap s] [--size WxH]');
  return args;
}

function python() {
  for (const candidate of ['bin/python', 'Scripts/python.exe']) {
    const found = path.join(ROOT, 'web', '.venv', candidate);
    if (existsSync(found)) return found;
  }
  return 'python';
}

function browser() {
  const named = process.env.AZIMUTH_CHROMIUM;
  if (named) return named;
  const shipped = '/opt/pw-browsers/chromium';
  return existsSync(shipped) ? shipped : undefined;
}

async function main() {
  const args = parse(process.argv.slice(2));
  const built = spawnSync(python(), [path.join(ROOT, 'scripts', 'harness.py'), '--build'], { stdio: 'inherit' });
  if (built.status !== 0) throw new Error('the harness page did not build');

  const launched = await chromium.launch({ headless: true, executablePath: browser(), args: ['--no-sandbox'] });
  const page = await launched.newPage({ viewport: { width: args.size[0], height: args.size[1] } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  // The harness has no tiles on purpose, so an image that fails to load is
  // the page working as built; an error the UI's own code raises is not.
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) errors.push(message.text());
  });
  await page.goto(pathToFileURL(PAGE).href, { waitUntil: 'load' });
  await page.waitForTimeout(args.gap * 1000);
  for (const name of args.probes) {
    const result = await page.evaluate(readFileSync(name, 'utf8'));
    console.log(`PROBE ${JSON.stringify(result)}`);
    await page.waitForTimeout(args.gap * 1000);
  }
  await page.screenshot({ path: args.out });
  await launched.close();
  for (const error of errors) console.error(`ERROR ${error}`);
  console.log(`captured ${args.out}`);
  return errors.length ? 1 : 0;
}

process.exitCode = await main();
