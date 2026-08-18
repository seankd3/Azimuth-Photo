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

console.log('page cache refuters passed');
