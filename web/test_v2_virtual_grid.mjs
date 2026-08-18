import assert from 'node:assert/strict';

import {
  measureGrid,
  placeGridCell,
  visibleGridRange,
} from './static/v2/kit/virtual-grid.js';

const large = measureGrid(1200, 220, 47_000);
const middle = visibleGridRange(large, large.height / 2, 900);
assert.equal(large.columns, 5);
assert.ok(middle.end - middle.start <= 50, 'the DOM window must stay bounded');
assert.ok(middle.start > 20_000 && middle.end < 27_000, 'the window follows the scroll');

const last = placeGridCell(large, 46_999);
assert.ok(last.top + last.width + large.padding <= large.height + 0.001);

const dense = measureGrid(1200, 150, 47_000);
assert.ok(dense.columns > large.columns);
assert.ok(dense.height < large.height);

const tiny = measureGrid(180, 320, 1);
assert.equal(tiny.columns, 1);
assert.deepEqual(visibleGridRange(tiny, 0, 900), { start: 0, end: 1 });

console.log('virtual grid refuters passed');
