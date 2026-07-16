import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { cacheCaptionRequest } from './static/js/mobile/caption_cache.js';

const cache = new Map();
let successLoads = 0;

const success = cacheCaptionRequest(
    cache,
    1,
    () => {
        successLoads += 1;
        return Promise.resolve({ has_caption: true, caption: 'A red barn.' });
    },
);
assert.equal(await success, await cache.get(1));
await cacheCaptionRequest(cache, 1, () => {
    successLoads += 1;
    return Promise.resolve(null);
});
assert.equal(successLoads, 1);
assert.equal(cache.has(1), true);

await cacheCaptionRequest(
    cache,
    2,
    () => Promise.resolve({ has_caption: false, not_found: true }),
);
assert.equal(cache.has(2), false);

await cacheCaptionRequest(
    cache,
    3,
    () => Promise.resolve({ has_caption: false, error: true, status: 503 }),
);
assert.equal(cache.has(3), false);

assert.equal(await cacheCaptionRequest(cache, 4, () => Promise.reject(new Error('offline'))), null);
assert.equal(cache.has(4), false);

const viewer = await readFile(
    new URL('./static/js/mobile/viewer.js', import.meta.url),
    'utf8',
);
assert.match(
    viewer,
    /return cacheCaptionRequest\(captionCache, id, \(\) => getImageCaption\(id\)\);/,
);

console.log('caption cache: successes retained; 404, error, and rejection retryable');
