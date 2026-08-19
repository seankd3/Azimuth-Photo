import assert from 'node:assert/strict';

import {
  measureGrid,
  placeGridCell,
  verticalNeighbour,
  visibleGridRange,
} from './static/v2/kit/virtual-grid.js';

// A library of 47,000 photographs in the shapes a real one has: landscapes,
// portraits, squares, the odd panorama, and a quarter not yet read.
const count = 47_000;
const aspects = new Float32Array(count);
for (let i = 0; i < count; i += 1) {
  const r = i % 20;
  aspects[i] = r < 10 ? 1.5 : r < 14 ? 0.667 : r < 16 ? 1 : r === 16 ? 3 : NaN;
}

const started = performance.now();
const large = measureGrid(1200, 220, aspects, count);
const took = performance.now() - started;
assert.ok(took < 200, `laying out 47,000 photographs took ${took.toFixed(0)} ms`);

// Every full row fills the width exactly, and nothing is cropped to do it:
// a cell's width is its aspect times the row height.
for (let row = 0; row < large.rows - 1; row += 1) {
  const start = large.rowStarts[row];
  const end = large.rowStarts[row + 1];
  const last = placeGridCell(large, end - 1);
  assert.ok(Math.abs((last.left + last.width + large.padding) - 1200) < 0.05, `row ${row} fills the width`);
  for (let at = start; at < end; at += 1) {
    const aspect = Number.isFinite(aspects[at]) ? aspects[at] : 1.5;
    assert.ok(Math.abs(large.widths[at] / large.rowHeights[row] - aspect) < 1e-3, 'a cell keeps its shape');
  }
  assert.ok(large.rowHeights[row] > 220 * 0.55 && large.rowHeights[row] <= 220 + 1e-6, `row ${row} never exceeds the chosen size`);
}
// The last row is not stretched.
assert.ok(large.rowHeights[large.rows - 1] <= 220 + 1e-6);

const middle = visibleGridRange(large, large.height / 2, 900);
assert.ok(middle.end - middle.start <= 80, 'the DOM window must stay bounded');
assert.ok(middle.start > 20_000 && middle.end < 27_000, 'the window follows the scroll');

const last = placeGridCell(large, count - 1);
assert.ok(last.top + last.height + large.padding <= large.height + 0.001);

// A shorter row holds more photographs: fewer rows, less height.
const dense = measureGrid(1200, 150, aspects, count);
assert.ok(dense.rows < large.rows);
assert.ok(dense.height < large.height);

const tiny = measureGrid(180, 320, aspects, 1);
assert.equal(tiny.rows, 1);
assert.deepEqual(visibleGridRange(tiny, 0, 900), { start: 0, end: 1 });

// Arrow keys: the cell above is the one whose centre is nearest, not "minus
// some number of columns" -- rows do not share columns here.
const below = verticalNeighbour(large, 0, 1);
const back = verticalNeighbour(large, below, -1);
assert.ok(below >= large.rowStarts[1] && below < large.rowStarts[2]);
assert.equal(back, 0);
assert.equal(verticalNeighbour(large, 0, -1), null);

console.log('virtual grid refuters passed');
