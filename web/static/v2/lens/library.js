import {
  measureGrid,
  placeGridCell,
  visibleGridRange,
} from '../kit/virtual-grid.js';
import { registerLens } from '../store/index.js';

const pendingTiles = new WeakMap();
const tileObserver = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      tileObserver.unobserve(entry.target);
      const load = pendingTiles.get(entry.target);
      pendingTiles.delete(entry.target);
      load?.();
    }
  }, { rootMargin: '500px' })
  : null;

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function forgetCell(cell) {
  const image = cell?.querySelector?.('img');
  if (!image) return;
  tileObserver?.unobserve(image);
  pendingTiles.delete(image);
}

function loadTile(image, photo, actions) {
  const load = () => actions.tile(photo.id).then(
    (source) => { image.src = source; },
    () => { image.remove(); },
  );
  if (!tileObserver) {
    load();
    return;
  }
  pendingTiles.set(image, load);
  tileObserver.observe(image);
}

function emptyState(actions) {
  const empty = element('div', 'empty-state');
  const mark = element('div', 'empty-mark');
  mark.setAttribute('aria-hidden', 'true');
  mark.append(element('i'), element('i'), element('i'));
  empty.append(mark, element('h2', '', actions.emptyTitle));
  empty.append(element('p', '', actions.emptyCopy));
  if (actions.emptyAction) {
    const button = element('button', 'primary-button', actions.emptyAction.label);
    button.type = 'button';
    button.addEventListener('click', actions.emptyAction.run);
    empty.append(button);
  }
  return empty;
}

function skeletonCell(index) {
  const cell = element('div', 'photo-skeleton');
  cell.dataset.index = index;
  cell.dataset.kind = 'skeleton';
  return cell;
}

function photoCell(photo, index, actions) {
  const cell = element('button', 'photo-cell');
  cell.type = 'button';
  cell.dataset.index = index;
  cell.dataset.kind = 'photo';
  cell.dataset.photoId = photo.id;
  cell.dataset.photoKey = `${photo.id}:${photo.tail}`;
  cell.setAttribute('aria-label', photo.tail || `Photo ${photo.id}`);
  cell.setAttribute('aria-pressed', 'false');

  const image = element('img');
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  cell.append(image);
  loadTile(image, photo, actions);
  cell.addEventListener('click', () => actions.select(photo, index));
  cell.addEventListener('dblclick', () => actions.open(photo, index));
  return cell;
}

function positionCell(cell, layout, index) {
  const place = placeGridCell(layout, index);
  cell.style.left = `${place.left}px`;
  cell.style.top = `${place.top}px`;
  cell.style.width = `${place.width}px`;
  cell.style.height = `${place.width}px`;
}

function reconcileGrid(grid, state, actions, layout, range) {
  const current = new Map(
    [...grid.children]
      .filter((cell) => cell.dataset.index !== undefined)
      .map((cell) => [cell.dataset.index, cell]),
  );
  const leftovers = new Set(grid.children);
  const desired = [];
  let firstMissing = null;
  let lastMissing = null;

  for (let index = range.start; index < range.end; index += 1) {
    const photo = state.photos.get(index);
    const key = String(index);
    let cell = current.get(key);
    const matches = photo
      ? cell?.dataset.photoKey === `${photo.id}:${photo.tail}`
      : cell?.dataset.kind === 'skeleton';
    if (!matches) {
      forgetCell(cell);
      leftovers.delete(cell);
      cell = photo ? photoCell(photo, index, actions) : skeletonCell(index);
    }
    if (!photo) {
      firstMissing ??= index;
      lastMissing = index + 1;
    }
    positionCell(cell, layout, index);
    cell.classList.toggle('is-selected', index === state.selectedIndex);
    if (cell.dataset.kind === 'photo') {
      cell.setAttribute('aria-pressed', String(index === state.selectedIndex));
    }
    desired.push(cell);
    leftovers.delete(cell);
  }

  for (const cell of leftovers) forgetCell(cell);
  grid.replaceChildren(...desired);
  if (firstMissing !== null) actions.need(firstMissing, lastMissing);
}

function renderGrid(grid, state, actions) {
  grid.setAttribute('aria-busy', String(state.loading));

  if (state.total === 0 && !state.loading) {
    for (const cell of grid.children) forgetCell(cell);
    grid.style.height = '';
    grid.replaceChildren(emptyState(actions));
    return;
  }

  const count = state.total || 18;
  const layout = measureGrid(grid.clientWidth, actions.cellSize, count);
  const range = visibleGridRange(layout, actions.scrollTop, actions.viewportHeight);
  grid.style.height = `${layout.height}px`;
  grid.dataset.columns = layout.columns;
  grid.dataset.cell = layout.cell;
  grid.dataset.pitch = layout.cell + layout.gap;
  grid.dataset.start = range.start;
  grid.dataset.end = range.end;

  if (state.loading && state.total === 0) {
    const loadingState = { ...state, photos: new Map(), selectedIndex: null };
    reconcileGrid(grid, loadingState, { ...actions, need: () => {} }, layout, range);
    return;
  }
  reconcileGrid(grid, state, actions, layout, range);
}

function renderInspector(panel, selected) {
  if (!selected) {
    panel.replaceChildren(element('div', 'inspector-empty', 'Select a photo to see its details.'));
    return;
  }
  const heading = element('div', 'inspector-heading');
  heading.append(element('p', 'eyebrow', 'Photo'), element('h2', '', selected.tail || 'Untitled'));
  const facts = element('dl', 'facts');
  const rows = [
    ['Taken', selected.date_taken],
    ['Camera', [selected.camera_make, selected.camera_model].filter(Boolean).join(' ')],
    ['Lens', selected.lens],
    ['Dimensions', selected.width && selected.height ? `${selected.width} × ${selected.height}` : ''],
  ];
  for (const [label, value] of rows) {
    if (!value) continue;
    facts.append(element('dt', '', label), element('dd', '', String(value)));
  }
  panel.replaceChildren(heading, facts);
}

registerLens('library', { renderGrid, renderInspector });
