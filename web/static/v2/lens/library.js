import { title, chapters as chaptersIn } from '../kit/days.js';
import { presence } from '../kit/presence.js';
import { icon } from '../kit/icons.js';
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
let breaks = null;
let breaksFrom = null;
let breaksVersion = 0;

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


const dayTitle = (day, count) => `${title(day)} · ${count.toLocaleString()}`;

function collectBreaks(state) {
  // Chapters exist where their arithmetic is exact: a date-sorted library
  // with the days list summing to precisely the photographs shown. The list
  // arrives newest first and the oldest sort reads it backwards. Anywhere
  // else the grid is flat — a misplaced header is worse than none.
  const dated = state.sort === 'newest' || state.sort === 'oldest';
  const days = dated && (state.days || []).length > 1
    ? (state.sort === 'oldest' ? [...state.days].reverse() : state.days)
    : null;
  if (breaksFrom && days !== null && breaksFrom.list === state.days && breaksFrom.sort === state.sort
      && state.total === breaksFrom.total) return;
  if (!days && !breaksFrom) return;
  breaksFrom = days ? { list: state.days, sort: state.sort, total: state.total } : null;
  breaks = null;
  breaksVersion += 1;
  if (!days) return;
  // The same walk the timeline rail makes, from the kit: empty when the
  // counts and the total disagree, so no chapter sits at a wrong index.
  const found = chaptersIn(state.days, state.sort, state.total);
  if (found.length) breaks = new Map(found.map((c) => [c.index, dayTitle(c.day, c.count)]));
}

function currentLayout(width, rowHeight, count) {
  const key = `${width}:${rowHeight}:${count}:${aspectsVersion}:${breaksVersion}`;
  if (key !== layoutKey) {
    layout = measureGrid(width, rowHeight, aspects, count, { breaks });
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
  const { state } = presence({ ...photo, placed: undefined }, Boolean(source));
  const empty = state === 'here' ? '' : state;
  if (cell.dataset.empty !== empty) cell.dataset.empty = empty;
  if (image.dataset.source === source) return;
  image.dataset.source = source;
  image.classList.remove('is-loaded');
  if (source) {
    image.src = source;
    if (image.complete && image.naturalWidth) image.classList.add('is-loaded');
    else image.onload = () => image.classList.add('is-loaded');
  } else image.removeAttribute('src');
}

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function emptyState(actions) {
  const empty = element('div', 'empty-state');
  const mark = element('div', 'empty-mark');
  mark.innerHTML = '<svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><g fill="none" stroke="currentColor"><circle cx="16" cy="16" r="13" stroke-width="1.2" opacity=".5"></circle><path d="M16 3v4M16 25v4M3 16h4M25 16h4" stroke-width="1.2" opacity=".5"></path><path d="M16 8.5 20.5 21 16 17.8 11.5 21Z" fill="var(--accent)" stroke="none"></path></g></svg>';
  mark.setAttribute('aria-hidden', 'true');
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
  // The key carries the stacking too: a frame that joined or left a stack
  // is a different cell, rebuilt with its band and badge.
  cell.dataset.photoKey = `${photo.id}:${photo.tail}:${photo.stack_of || 0}:${photo.stack || 0}`;
  cell.setAttribute('aria-pressed', 'false');

  const image = element('img');
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  const flag = element('span', 'pick-flag');
  flag.setAttribute('aria-hidden', 'true');
  cell.append(image, flag);
  // A star earned by the ranking, worn where the ranking is the order.
  if (photo.stars > 0) cell.dataset.stars = photo.stars;
  if (photo.stack) {
    // A cover fronts its stack and wears its count; the badge folds it or
    // opens it in place: the members take their seats right after the
    // cover and leave again on the next click. A span, because the cell is
    // already a button and a button may not hold another.
    const open = actions.stackOpen?.(photo.id);
    const badge = element('span', 'stack-badge' + (open ? ' is-open' : ''));
    badge.append(icon('stack'), String(photo.stack + 1));
    badge.setAttribute('role', 'button');
    badge.title = open ? `A stack of ${photo.stack + 1} — fold it (S)` : `A stack of ${photo.stack + 1} — open it (S)`;
    badge.addEventListener('click', (event) => {
      event.stopPropagation();
      actions.stack?.(photo, index);
    });
    cell.append(badge);
  }
  // A stack wears a band: cover and members alike, so a row of frames
  // reads as one set. Members carry the tie, covers the badge.
  if (photo.stack_of || photo.stack) cell.classList.add('is-stacked');
  if (photo.stack_of) cell.classList.add('is-member');
  cell.addEventListener('click', (event) => {
    // Clicking a photograph makes the grid the keyboard's surface — Y/N
    // and the cull keys must land here even if a search box held focus.
    cell.focus();
    actions.select(index, { shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey });
  });
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
  for (let index = range.start; index < range.end; index += 1) {
    const photo = state.photos.get(index);
    const key = String(index);
    let cell = current.get(key);
    const matches = photo
      ? cell?.dataset.photoKey === `${photo.id}:${photo.tail}:${photo.stack_of || 0}:${photo.stack || 0}`
      : cell?.dataset.kind === 'skeleton';
    if (!matches) cell = photo ? photoCell(photo, index, actions) : skeletonCell(index);
    positionCell(cell, layout, index, photo);
    // Marked wears the accent; the keyboard cursor is its own quieter ring,
    // so where you are is visible inside what you have.
    cell.classList.toggle('is-selected', Boolean(photo && state.marked?.has(photo.id)) || (!photo && index === state.selectedIndex));
    // The lone marked photograph wears the accent alone; the cursor's own
    // ring appears inside a set, or on a bare cursor with nothing marked.
    const alone = photo && state.marked?.size === 1 && state.marked.has(photo.id);
    cell.classList.toggle('is-focus', index === state.selectedIndex && !alone);
    if (cell.dataset.kind === 'photo') {
      showTile(cell, photo);
      const picked = photo.status === 'picked';
      cell.classList.toggle('is-picked', picked);
      cell.dataset.missing = photo.placed ? '0' : '1';
      const { state: where, said } = presence(photo, cell.dataset.empty === '');
      const why = where === 'here' ? '' : `, ${said.replace(/[.…]$/, '')}`;
      cell.setAttribute('aria-label', `${picked ? 'Picked, ' : ''}${photo.tail || `Photo ${photo.id}`}${why}`);
      cell.setAttribute('aria-pressed', String(index === state.selectedIndex));
      // A cell that stays keeps its badge; the badge keeps up with the set.
      const badge = cell.querySelector('.stack-badge');
      if (badge) {
        const open = Boolean(actions.stackOpen?.(photo.id));
        badge.classList.toggle('is-open', open);
        badge.title = open ? `A stack of ${photo.stack + 1} — fold it (S)` : `A stack of ${photo.stack + 1} — open it (S)`;
      }
    }
    desired.push(cell);
    leftovers.delete(cell);
  }

  // Chapter bands ride the same reconcile: absolutely placed, keyed by the
  // index they break at, kept only while their chapter is in range.
  const currentBands = new Map(
    [...grid.children]
      .filter((node) => node.dataset.band !== undefined)
      .map((node) => [node.dataset.band, node]),
  );
  for (const band of layout.bands || []) {
    if (band.index < range.start || band.index > range.end) continue;
    const key = String(band.index);
    let node = currentBands.get(key);
    if (!node) {
      node = element('div', 'grid-chapter');
      node.dataset.band = key;
    }
    if (node.dataset.title !== band.title) {
      // The day is the anchor; its count sits beside it, quieter.
      node.dataset.title = band.title;
      const at = band.title.lastIndexOf(' · ');
      node.replaceChildren(element('span', 'day', at > 0 ? band.title.slice(0, at) : band.title),
        element('span', 'count', at > 0 ? band.title.slice(at + 3) : ''));
    }
    node.style.top = `${band.top}px`;
    desired.push(node);
    leftovers.delete(node);
  }

  // Cells are absolutely positioned, so DOM order carries nothing: only what
  // left the range is removed and only what entered is attached. Cells that
  // stay are never detached — their focus, and the breathing of a tile still
  // being made, survive every scroll frame.
  for (const leftover of leftovers) leftover.remove();
  for (const cell of desired) {
    if (cell.parentNode !== grid) grid.append(cell);
  }
  // What is on screen, every frame: pages not yet held are asked for, and
  // the cache learns which pages a refresh must keep fresh.
  actions.need(range.start, range.end);
  actions.look(desired.filter((cell) => cell.dataset.kind === 'photo').map((cell) => Number(cell.dataset.photoId)));
}

function renderGrid(grid, state, actions) {
  grid.setAttribute('aria-busy', String(state.loading));
  grid.classList.toggle('is-best', state.sort === 'best' || state.sort === 'stars');

  if (state.total === 0 && !state.loading) {
    grid.style.height = '';
    // The same empty state stays put across renders — rebuilding it twice a
    // second would blink its hover and drop a click mid-swap.
    const key = `${actions.emptyTitle}|${actions.emptyCopy}|${actions.emptyAction?.label || ''}`;
    if (grid.firstElementChild?.dataset.emptyKey !== key) {
      const empty = emptyState(actions);
      empty.dataset.emptyKey = key;
      grid.replaceChildren(empty);
    }
    return;
  }

  const count = state.total || 18;
  collectAspects(state);
  collectBreaks(state);
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

// The first cell under a scroll position — what the timeline's marker rides.
function indexAt(scrollTop) {
  return layout && layout.count ? anchorOf(layout, scrollTop).index : null;
}

// What the selection is, said in facts. One photograph: its own. Several:
// the set's -- how many, the span of days, the cameras, the cull tally --
// from the rows the window already holds.
function renderInspector(panel, selected, actions = {}) {
  const marked = actions.marked;
  if (marked && marked.size > 1) {
    const rows = [...(actions.photos?.values() || [])].filter((p) => marked.has(p.id));
    const days = rows.map((p) => (p.date_taken || '').slice(0, 10)).filter(Boolean).sort();
    const cameras = [...new Set(rows.map((p) => p.camera_model).filter(Boolean))];
    const tally = { picked: 0, trashed: 0, unflagged: 0 };
    for (const p of rows) tally[p.status in tally ? p.status : 'unflagged'] += 1;
    const heading = element('div', 'inspector-heading');
    heading.append(element('p', 'eyebrow', 'Selection'), element('h2', '', `${marked.size.toLocaleString()} photographs`));
    const facts = element('dl', 'facts');
    const said = [
      ['Loaded', rows.length < marked.size ? `${rows.length.toLocaleString()} of them on hand` : ''],
      ['Taken', days.length ? (days[0] === days.at(-1) ? title(days[0]) : `${title(days[0], { weekday: false })} – ${title(days.at(-1), { weekday: false })}`) : ''],
      ['Cameras', cameras.join(' · ')],
      ['Cull', [tally.picked && `${tally.picked} picked`, tally.unflagged && `${tally.unflagged} unflagged`].filter(Boolean).join(' · ')],
      ['Size', rows.some((p) => p.file_size) ? `${(rows.reduce((n, p) => n + (p.file_size || 0), 0) / 1e9).toFixed(2)} GB` : ''],
    ];
    for (const [label, value] of said) if (value) facts.append(element('dt', '', label), element('dd', '', String(value)));
    panel.replaceChildren(heading, facts);
    return;
  }
  if (!selected) {
    // Nothing selected: the library at a glance, from what the window
    // already holds, instead of an empty column.
    const counts = actions.counts || {};
    const drives = actions.drives || [];
    const glance = element('div', 'glance');
    const heading = element('div', 'inspector-heading');
    heading.append(element('p', 'eyebrow', 'Library'), element('h2', '', `${(counts.photos || 0).toLocaleString()} photographs`));
    const facts = element('dl', 'facts');
    const rows = [
      ['Starred', counts.starred ? counts.starred.toLocaleString() : ''],
      ['In Trash', counts.trash ? counts.trash.toLocaleString() : ''],
      ['Drives', drives.length ? drives.map((d) => `${d.label || d.root}${d.attached ? '' : ' · away'}`).join(' · ') : ''],
      ['Working', actions.working ? [actions.working.word, actions.working.left ? `${actions.working.left.toLocaleString()} left` : ''].filter(Boolean).join(' · ') : ''],
    ];
    for (const [label, value] of rows) if (value) facts.append(element('dt', '', label), element('dd', '', value));
    glance.append(heading, facts, element('p', 'glance-hint', 'Select a photograph to see its facts. ? shows every key.'));
    panel.replaceChildren(glance);
    return;
  }
  const heading = element('div', 'inspector-heading');
  // The heading is the photograph's own name; the folder is a fact below.
  heading.append(element('p', 'eyebrow', 'Photo'), element('h2', '', (selected.tail || 'Untitled').split('/').pop()));
  const facts = element('dl', 'facts');
  // The score, with its provenance: earned from the rounds this photograph
  // was actually in, predicted by the taste direction where it was not, and
  // never a black box. 1200 with no rounds means the ranking has not
  // reached it yet.
  const rounds = selected.rounds ?? null;
  const elo = Math.round(selected.elo || 1200);
  const score = rounds === null && elo === 1200 ? ''
    : rounds ? `${elo.toLocaleString()} · ${rounds} round${rounds === 1 ? '' : 's'}`
      : elo !== 1200 ? `${elo.toLocaleString()} · predicted`
        : 'Not ranked yet';
  // What the develop recipe holds: a crop, adjustments, or both.
  let edited = '';
  try {
    const recipe = selected.develop ? JSON.parse(selected.develop) : {};
    const keys = Object.keys(recipe);
    const cropped = keys.some((k) => k.startsWith('Crop'));
    const adjusted = keys.some((k) => !k.startsWith('Crop'));
    edited = [cropped && 'Cropped', adjusted && 'Adjusted'].filter(Boolean).join(' · ');
    if (edited) edited += ' — D opens Develop';
  } catch { edited = selected.develop ? 'Edited — D opens Develop' : ''; }
  const exposure = [
    selected.f_number && `ƒ/${selected.f_number}`,
    selected.exposure_time && (selected.exposure_time >= 1 ? `${selected.exposure_time}s` : `1/${Math.round(1 / selected.exposure_time)}s`),
    selected.iso && `ISO ${selected.iso}`,
    selected.focal_length && `${Math.round(selected.focal_length)}mm`,
  ].filter(Boolean).join(' · ');
  const place = selected.lat !== undefined && selected.lon !== undefined
    ? `${Math.abs(selected.lat).toFixed(4)}° ${selected.lat >= 0 ? 'N' : 'S'}, ${Math.abs(selected.lon).toFixed(4)}° ${selected.lon >= 0 ? 'E' : 'W'}` : '';
  const stars = selected.stars ? `${'★'.repeat(selected.stars)} · ` : '';
  const folder = selected.tail && selected.tail.includes('/') ? selected.tail.slice(0, selected.tail.lastIndexOf('/')) : '';
  // A fact that should always speak says Unknown rather than vanishing;
  // one the file may simply not carry is left out.
  const unknown = selected.hash ? 'Unknown' : 'Reading…';
  const rows = [
    ['Score', score ? stars + score : (stars ? stars.slice(0, -3) : '')],
    ['Edited', edited],
    ['Names', (selected.names || []).join(' · ')],
    ['Cull', !selected.hash ? 'Reading…'
      : selected.status === 'picked' ? 'Picked' : selected.status === 'trashed' ? 'Rejected' : 'Unflagged'],
    ['Where', presence(selected, true).said],
    ['Taken', selected.date_taken || unknown],
    ['Exposure', exposure],
    ['Camera', [selected.camera_make, selected.camera_model].filter(Boolean).join(' ') || unknown, { chip: selected.camera_model && { is: 'camera', values: [selected.camera_model] } }],
    ['Place', place],
    ['Lens', selected.lens],
    ['Dimensions', selected.width && selected.height ? `${selected.width} × ${selected.height}` : ''],
    ['Size', selected.file_size ? `${(selected.file_size / 1e6).toFixed(1)} MB` : ''],
    ['Folder', folder, { folder }],
  ];
  for (const [label, value, link] of rows) {
    if (!value) continue;
    facts.append(element('dt', '', label));
    const cell = element('dd', '', String(value));
    // A fact that names a place in the library is a way to go there.
    if (link?.folder && actions.showFolder) {
      cell.classList.add('is-link');
      cell.title = 'Browse this folder';
      cell.addEventListener('click', () => actions.showFolder(link.folder));
    } else if (link?.chip && actions.applyChip) {
      cell.classList.add('is-link');
      cell.title = 'Narrow to this camera';
      cell.addEventListener('click', () => actions.applyChip(link.chip));
    } else {
      cell.title = 'Click to copy';
      cell.addEventListener('click', () => {
        navigator.clipboard?.writeText(String(value)).then(() => actions.notify?.('Copied.')).catch(() => {});
      });
    }
    facts.append(cell);
  }
  panel.replaceChildren(heading, facts);
}

registerLens('library', { indexAt, neighbour, place, renderGrid, renderInspector });
