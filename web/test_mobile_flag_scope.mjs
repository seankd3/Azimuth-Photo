import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { restoresFilteredMembership } from './static/js/mobile/flag_scope.js';

const images = [{ id: 1, flag: 'picked' }];
const flags = new Map([
    [1, 'picked'],
    [2, 'picked'],
    [3, 'rejected'],
]);
const flagOf = (id) => flags.get(id) || 'unflagged';

assert.equal(restoresFilteredMembership(images, [2], flagOf, 'picked'), true);
assert.equal(restoresFilteredMembership(images, [3], flagOf, 'picked'), false);
assert.equal(restoresFilteredMembership(images, [1], flagOf, 'picked'), false);
assert.equal(restoresFilteredMembership(images, [2], flagOf, ''), false);

const timeline = await readFile(
    new URL('./static/js/mobile/timeline.js', import.meta.url),
    'utf8',
);
assert.match(
    timeline,
    /if \(restoresFilteredMembership\(images, ids, flagOf, scope\.flag\)\) \{\s+void reload\(\);/,
);

console.log('filtered flag membership: reload only when a missing photo belongs again');
