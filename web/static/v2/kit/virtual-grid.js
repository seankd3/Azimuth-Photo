const DEFAULT_GAP = 4;
const DEFAULT_PADDING = 4;

export function measureGrid(width, preferredCell, count, {
  gap = DEFAULT_GAP,
  padding = DEFAULT_PADDING,
} = {}) {
  const available = Math.max(1, Number(width) - (2 * padding));
  const preferred = Math.max(1, Number(preferredCell));
  const columns = Math.max(1, Math.floor((available + gap) / (preferred + gap)));
  const cell = (available - (gap * (columns - 1))) / columns;
  const total = Math.max(0, Number(count));
  const rows = Math.ceil(total / columns);
  const height = rows === 0 ? 0 : (2 * padding) + (rows * cell) + ((rows - 1) * gap);
  return Object.freeze({ cell, columns, count: total, gap, height, padding, rows });
}

export function visibleGridRange(layout, scrollTop, viewportHeight, overscan = 2) {
  if (layout.count === 0) return Object.freeze({ start: 0, end: 0 });
  const pitch = layout.cell + layout.gap;
  const top = Math.max(0, Number(scrollTop));
  const bottom = top + Math.max(0, Number(viewportHeight));
  const firstRow = Math.max(
    0,
    Math.floor((top - layout.padding) / pitch) - overscan,
  );
  const lastRow = Math.min(
    layout.rows,
    Math.ceil((bottom - layout.padding) / pitch) + overscan,
  );
  return Object.freeze({
    start: firstRow * layout.columns,
    end: Math.min(layout.count, lastRow * layout.columns),
  });
}

export function placeGridCell(layout, index) {
  const row = Math.floor(index / layout.columns);
  const column = index % layout.columns;
  const pitch = layout.cell + layout.gap;
  return Object.freeze({
    left: layout.padding + (column * pitch),
    top: layout.padding + (row * pitch),
    width: layout.cell,
  });
}
