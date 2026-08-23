// Pure geometry for a justified photo grid: rows fill the width, each cell
// keeps its photograph's own shape, and nothing here knows about DOM or
// product state. Given every photograph's aspect (or NaN for one not yet
// read), a target row height, and the width, it lays every row out in one
// pass -- 157,000 photographs in a few milliseconds -- and answers where a
// cell is, which cells a scroll position shows, and which cell sits above
// or below another.

const DEFAULT_GAP = 4;
const DEFAULT_PADDING = 4;
// The shape assumed for a photograph whose dimensions have not been read yet:
// a 3:2 frame, which most cameras make. The row it sits in is re-laid when the
// real shape arrives, and the viewport is anchored across that change.
const ASSUMED_ASPECT = 1.5;

const DEFAULT_BAND = 34;

export function measureGrid(width, rowHeight, aspects, count, {
  gap = DEFAULT_GAP,
  padding = DEFAULT_PADDING,
  // Chapter breaks: index -> title. A break closes the row before it and
  // reserves a title band above the row it starts, so chapters are part of
  // the same pure geometry rather than a second layout pass.
  breaks = null,
  bandHeight = DEFAULT_BAND,
} = {}) {
  const total = Math.max(0, Math.floor(Number(count)));
  const available = Math.max(1, Number(width) - (2 * padding));
  const target = Math.max(1, Number(rowHeight));
  const aspectAt = (index) => {
    const value = aspects ? Number(aspects[index]) : NaN;
    return Number.isFinite(value) && value > 0 ? value : ASSUMED_ASPECT;
  };

  const rowStarts = [];
  const rowTops = [];
  const rowHeights = [];
  const rowOf = new Uint32Array(total);
  const lefts = new Float32Array(total);
  const widths = new Float32Array(total);
  const bands = [];

  let top = padding;
  let index = 0;
  while (index < total) {
    if (breaks && breaks.has(index)) {
      bands.push({ top, index, title: breaks.get(index) });
      top += bandHeight;
    }
    // A row is as many photographs as fit at the target height plus the one
    // that overflows, all scaled down until the row is exactly the width. So
    // a row is never taller than the target -- the size the person chose is a
    // ceiling -- and the only rows shorter than that by much are a lone
    // panorama and the last row, which is left at the target rather than
    // stretched across the width.
    let sum = 0;
    let end = index;
    let closed = false;
    while (end < total) {
      if (breaks && end > index && breaks.has(end)) break;
      sum += aspectAt(end);
      end += 1;
      if ((sum * target) + (gap * (end - index - 1)) >= available) {
        closed = true;
        break;
      }
    }
    const length = end - index;
    const height = closed ? (available - (gap * (length - 1))) / sum : target;

    rowStarts.push(index);
    rowTops.push(top);
    rowHeights.push(height);
    let left = padding;
    for (let at = index; at < end; at += 1) {
      rowOf[at] = rowStarts.length - 1;
      lefts[at] = left;
      widths[at] = aspectAt(at) * height;
      left += widths[at] + gap;
    }
    top += height + gap;
    index = end;
  }

  const height = total === 0 ? 0 : top - gap + padding;
  return Object.freeze({
    count: total, gap, padding, width: available, rowHeight: target,
    rowStarts, rowTops, rowHeights, rowOf, lefts, widths, height,
    rows: rowStarts.length, bands,
  });
}

export function placeGridCell(layout, index) {
  const row = layout.rowOf[index];
  return Object.freeze({
    left: layout.lefts[index],
    top: layout.rowTops[row],
    width: layout.widths[index],
    height: layout.rowHeights[row],
  });
}

function rowAt(layout, y) {
  // The last row whose top is at or above y.
  let low = 0;
  let high = layout.rows - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (layout.rowTops[mid] <= y) low = mid;
    else high = mid - 1;
  }
  return low;
}

export function visibleGridRange(layout, scrollTop, viewportHeight, overscan = 2) {
  if (layout.count === 0) return Object.freeze({ start: 0, end: 0 });
  const top = Math.max(0, Number(scrollTop));
  const bottom = top + Math.max(0, Number(viewportHeight));
  const firstRow = Math.max(0, rowAt(layout, top) - overscan);
  const lastRow = Math.min(layout.rows - 1, rowAt(layout, bottom) + overscan);
  const end = lastRow + 1 < layout.rows ? layout.rowStarts[lastRow + 1] : layout.count;
  return Object.freeze({ start: layout.rowStarts[firstRow], end });
}

export function verticalNeighbour(layout, index, direction) {
  // The cell in the row above (direction < 0) or below whose horizontal centre
  // is nearest this cell's, which is what an arrow key means in a grid whose
  // rows do not share columns.
  if (layout.count === 0) return null;
  const row = layout.rowOf[index] + (direction < 0 ? -1 : 1);
  if (row < 0 || row >= layout.rows) return null;
  const centre = layout.lefts[index] + (layout.widths[index] / 2);
  const start = layout.rowStarts[row];
  const end = row + 1 < layout.rows ? layout.rowStarts[row + 1] : layout.count;
  let best = start;
  let distance = Infinity;
  for (let at = start; at < end; at += 1) {
    const gap = Math.abs(layout.lefts[at] + (layout.widths[at] / 2) - centre);
    if (gap < distance) {
      distance = gap;
      best = at;
    }
  }
  return best;
}
