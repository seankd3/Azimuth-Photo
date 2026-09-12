import assert from 'node:assert/strict';

import { PageCache } from './static/v2/kit/page-cache.js';

const requests = [];
const pages = [];
const errors = [];
const cache = new PageCache({
  pageSize: 200,
  load: (offset) => new Promise((resolve, reject) => requests.push({ offset, reject, resolve })),
  onPage: (values, total) => pages.push({ total, values }),
  onError: (error, offset) => errors.push({ error, offset }),
});

let generation = cache.reset();
cache.seed(generation, [{ id: 1 }], 47_000);
const first = cache.ensure(1_037);
const duplicate = cache.ensure(1_199);
assert.equal(requests.length, 1, 'one page has only one in-flight request');
assert.equal(requests[0].offset, 1_000);
requests[0].resolve([{ id: 2 }]);
await Promise.all([first, duplicate]);
assert.equal(pages.at(-1).values.get(1_000).id, 2);

const stale = cache.ensure(1_400);
assert.equal(requests.at(-1).offset, 1_400);
generation = cache.reset();
cache.seed(generation, [{ id: 3 }], 1);
requests.at(-1).resolve([{ id: 4 }]);
await stale;
assert.equal(pages.at(-1).values.has(1_400), false, 'a prior generation cannot repaint the grid');

generation = cache.reset();
cache.seed(generation, [], 600);
const failed = cache.ensure(400);
requests.at(-1).reject(new Error('offline'));
await failed;
assert.equal(errors.length, 1);
assert.equal(errors[0].offset, 400);
const loadCount = requests.length;
await cache.ensure(400);
assert.equal(requests.length, loadCount, 'a failed visible page does not create a retry storm');

generation = cache.reset();
cache.seed(generation, [{ id: 1, hash: 'a', status: 'unflagged' }, { id: 2, hash: 'a', status: 'unflagged' }, { id: 3, hash: 'b', status: 'unflagged' }], 3);
const touched = cache.patch((item) => item.hash === 'a', (item) => ({ ...item, status: 'picked' }));
assert.equal(touched, 2, 'a change to an identity lands on every row that holds it');
assert.deepEqual([...pages.at(-1).values.values()].map((item) => item.status), ['picked', 'picked', 'unflagged']);

generation = cache.reset();
cache.seed(generation, [{ id: 1 }], 47_000);
for (const offset of [200, 400, 20_000, 20_200]) {
  const ensured = cache.ensure(offset);
  requests.at(-1).resolve([{ id: offset }]);
  await ensured;
}
const stormed = cache.ensure(30_000);
requests.at(-1).reject(new Error('offline'));
await stormed;
await cache.ensureRange(20_000, 20_400);
const before = requests.length;
const refreshed = cache.refresh(47_000);
assert.equal(requests.length - before, 2, 'a refresh re-reads only the pages around what the window asked for');
assert.deepEqual(requests.slice(before).map((r) => r.offset).sort((a, b) => a - b), [20_000, 20_200]);
for (const request of requests.slice(before)) request.resolve([{ id: request.offset }]);
await refreshed;
assert.equal(cache.values.has(200), false, 'pages far from the window are let go');
assert.equal(cache.values.has(20_200), true, 'pages in the window stay');
assert.equal(cache.failed.has(30_000), false, 'a failed page is owed again after a refresh');

console.log('page cache refuters passed');
