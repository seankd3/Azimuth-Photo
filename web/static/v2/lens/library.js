import {
  measureGrid,
  placeGridCell,
  verticalNeighbour,
  visibleGridRange,
} from '../kit/virtual-grid.js';
import { registerLens } from '../store/index.js';

// The layout is a pure function of the width, the row height, and every
// photograph's shape. Shapes arrive with the rows (width and height from the
// embedded metadata), so the lens keeps one array of aspects the size of the
// library, fills it from whatever rows are loaded, and lays out again only
// when something it depends on changed. Unknown shapes are assumed 3:2 and
// the viewport is anchored to its first cell across a re-layout, so a row
// above changing height does not move what the person is looking at.
let aspects = new Float32Array(0);
let aspectsVersion = 0;
let layout = null;
let layoutKey = '';

function collectAspects(state) {
  if (aspects.length !== state.total) {
    aspects = new Float32Array(state.total).fill(NaN);
    aspectsVersion += 1;
  }
  for (const [index, photo] of state.photos) {
    if (index >= aspects.length) continue;
    const sideways = photo.rotate === 90 || photo.rotate === 270;
    const aspect = photo.width > 0 && photo.height > 0
      ? (sideways ? photo.height / photo.width : photo.width / photo.height)
      : NaN;
    if (Object.is(aspects[index], aspect) || (Number.isNaN(aspects[index]) && Number.isNaN(aspect))) continue;
    aspects[index] = aspect;
    aspectsVersion += 1;
  }
}

function currentLayout(width, rowHeight, count) {
  const key = `${width}:${rowHeight}:${count}:${aspectsVersion}`;
  if (key !== layoutKey) {
    layout = measureGrid(width, rowHeight, aspects, count);
    layoutKey = key;
  }
  return layout;
}

// A cell's picture is a file the browser reads itself. The row says whether
// the answer exists (`photo.tile` is a URL or null); a cell with none shows
// its placeholder and fills the moment a refresh hands it a URL. Nothing here
// fetches, decodes, retries, or watches the viewport -- `loading="lazy"` is
// the browser doing that.
function showTile(cell, photo) {
  const image = cell.querySelector('img');
  const source = photo.tile || '';
  // An empty cell is one of three honest states: the worker will make the
  // picture (pending), it was tried and cannot be made (unshowable), or no
  // copy is on a drive that is here (away). The cell says which.
  const state = source ? '' : photo.tile_failed ? 'unshowable' : photo.reachable ? 'pending' : 'away';
  if (cell.dataset.empty !== state) cell.dataset.empty = state;
  if (image.dataset.source === source) return;
  image.dataset.source = source;
  if (source) image.src = source;
  else image.removeAttribute('src');
}

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
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
  cell.setAttribute('aria-pressed', 'false');

  const image = element('img');
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  const flag = element('span', 'pick-flag');
  flag.setAttribute('aria-hidden', 'true');
  cell.append(image, flag);
  cell.addEventListener('click', (event) => actions.select(index, {
    shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey,
  }));
  cell.draggable = true;
  cell.addEventListener('dragstart', (event) => actions.drag?.(index, event));
  cell.addEventListener('dblclick', () => actions.open(index));
  return cell;
}

function positionCell(cell, layout, index, photo) {
  const place = placeGridCell(layout, index);
  cell.style.left = `${place.left}px`;
  cell.style.top = `${place.top}px`;
  cell.style.width = `${place.width}px`;
  cell.style.height = `${place.height}px`;
  const turn = photo?.rotate || 0;
  cell.dataset.turn = turn;
  const image = cell.querySelector('img');
  if (!image) return;
  // A turned picture: the image is laid out at the cell's turned size and
  // rotated about its centre, so the tile is shown sideways without a second
  // tile ever being made.
  if (turn === 90 || turn === 270) {
    image.style.width = `${place.height}px`;
    image.style.height = `${place.width}px`;
    image.style.left = `${(place.width - place.height) / 2}px`;
    image.style.top = `${(place.height - place.width) / 2}px`;
  } else {
    image.style.width = '';
    image.style.height = '';
    image.style.left = '';
    image.style.top = '';
  }
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
      leftovers.delete(cell);
      cell = photo ? photoCell(photo, index, actions) : skeletonCell(index);
    }
    if (!photo) {
      firstMissing ??= index;
      lastMissing = index + 1;
    }
    positionCell(cell, layout, index, photo);
    cell.classList.toggle('is-selected',
      index === state.selectedIndex || Boolean(photo && state.marked?.has(photo.id)));
    if (cell.dataset.kind === 'photo') {
      showTile(cell, photo);
      const picked = photo.status === 'picked';
      cell.classList.toggle('is-picked', picked);
      cell.dataset.missing = photo.placed ? '0' : '1';
      const why = !photo.placed ? ', missing' : cell.dataset.empty === 'unshowable' ? ', cannot be shown' : cell.dataset.empty === 'away' ? ', drive away' : '';
      cell.setAttribute('aria-label', `${picked ? 'Picked, ' : ''}${photo.tail || `Photo ${photo.id}`}${why}`);
      cell.setAttribute('aria-pressed', String(index === state.selectedIndex));
    }
    desired.push(cell);
    leftovers.delete(cell);
  }

  grid.replaceChildren(...desired);
  if (firstMissing !== null) actions.need(firstMissing, lastMissing);
  actions.look(desired.filter((cell) => cell.dataset.kind === 'photo').map((cell) => Number(cell.dataset.photoId)));
}

function renderGrid(grid, state, actions) {
  grid.setAttribute('aria-busy', String(state.loading));

  if (state.total === 0 && !state.loading) {
    grid.style.height = '';
    grid.replaceChildren(emptyState(actions));
    return;
  }

  const count = state.total || 18;
  collectAspects(state);
  const before = layout;
  const anchor = before && before.count === count && actions.scrollTop > 0
    ? anchorOf(before, actions.scrollTop)
    : null;
  const layoutNow = currentLayout(grid.clientWidth, actions.rowHeight, count);
  let scrollTop = actions.scrollTop;
  if (anchor && layoutNow !== before) {
    // Keep the first visible cell where it was on screen across the re-layout.
    scrollTop = Math.max(0, placeGridCell(layoutNow, anchor.index).top - anchor.offset);
    if (Math.abs(scrollTop - actions.scrollTop) >= 1) actions.scrollTo(scrollTop);
  }
  const range = visibleGridRange(layoutNow, scrollTop, actions.viewportHeight);
  grid.style.height = `${layoutNow.height}px`;

  if (state.loading && state.total === 0) {
    const loadingState = { ...state, photos: new Map(), selectedIndex: null };
    reconcileGrid(grid, loadingState, { ...actions, need: () => {}, look: () => {} }, layoutNow, range);
    return;
  }
  reconcileGrid(grid, state, actions, layoutNow, range);
}

function anchorOf(current, scrollTop) {
  const range = visibleGridRange(current, scrollTop, 1, 0);
  const index = Math.min(range.start, current.count - 1);
  return { index, offset: placeGridCell(current, index).top - scrollTop };
}

// What the shell needs from the geometry without owning it: where a cell is,
// and which cell an arrow key means.
function place(index) {
  return layout && index < layout.count ? placeGridCell(layout, index) : null;
}

function neighbour(index, direction) {
  return layout ? verticalNeighbour(layout, index, direction) : null;
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
    ['Cull', !selected.hash ? 'Reading…'
      : selected.status === 'picked' ? 'Picked' : selected.status === 'trashed' ? 'Rejected' : 'Unflagged'],
    ['Where', !selected.placed ? 'Missing — no drive holds it' : selected.reachable ? 'Here' : 'On a drive that is away'],
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

registerLens('library', { neighbour, place, renderGrid, renderInspector });
