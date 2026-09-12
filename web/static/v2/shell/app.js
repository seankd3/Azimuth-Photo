import { acceptDrops, IDS } from '../kit/drop.js';
import { why } from '../kit/why.js';
import { showMenu, hideMenu } from '../kit/menu.js';
import { library as product } from '../net/index.js';
import { PageCache } from '../kit/page-cache.js';
import { getLens, read, subscribe, update } from '../store/index.js';
import { createCropSurface } from './crop.js';
import { createEditPanel } from './editpanel.js';
import { createCullWorkflow, CULL_MENU } from './cull.js';
import { createPeoplePanel } from './people.js';
import { createAlbumsPanel } from './albums.js';
import { createFilterBar } from './filters.js';
import { createIntakeWorkflow } from './intake.js';
import { createLabelsPanel } from './labels.js';
import { createLoupe } from './loupe.js';
import { createRankWorkflow } from './rank.js';
import { createSearchCards } from './searchcards.js';
import { createTrashWorkflow } from './trash.js';
import { createUndo } from './undo.js';
import { recall, remember } from '../kit/remembered.js';
import { presence } from '../kit/presence.js';
import { icon } from '../kit/icons.js';
import { createTimeline } from './timeline.js';

// A page is at least a viewport at the densest setting, so a 4K window
// does not straddle three pages on every scroll frame; never past what the
// library serves at once.
const PAGE = Math.min(500, Math.max(200, Math.ceil(window.innerWidth / 150) * Math.ceil(window.innerHeight / 150)));
const CONTEXTBAR_HEIGHT = 46;
const library = getLens('library');
const workspace = document.querySelector('.workspace');
const grid = document.querySelector('[data-grid]');
const timeline = createTimeline({
  workspace,
  before: grid,
  top: CONTEXTBAR_HEIGHT,
  place: (index) => library.place(index),
  indexAt: (scrollTop) => library.indexAt(scrollTop),
  scrollTo: (top) => { workspace.scrollTop = top; },
});
const inspector = document.querySelector('[data-inspector]');
const homeDialog = document.querySelector('[data-home-dialog]');
const homeForm = document.querySelector('[data-home-form]');
const homePath = document.querySelector('[data-home-path]');
const homeError = document.querySelector('[data-home-error]');
const driveDialog = document.querySelector('[data-drive-dialog]');
const driveForm = document.querySelector('[data-drive-form]');
const driveError = document.querySelector('[data-drive-error]');
const loupe = document.querySelector('[data-loupe]');
const loupeImage = document.querySelector('[data-loupe-image]');
const shell = document.querySelector('.app-shell');
const loupeStrip = document.querySelector('[data-loupe-strip]');
const loupeOpen = () => read().view === 'loupe';
const loupeView = createLoupe({
  stage: loupe,
  image: loupeImage,
  inset: () => (loupe.classList.contains('is-full') ? 0 : loupeStrip.offsetHeight),
  onTrouble: () => {
    // The file was promised and did not load — a black stage explains itself.
    const note = document.querySelector('[data-loupe-note]');
    note.textContent = presence({ tile_failed: true }, false).said;
    note.hidden = false;
  },
});

// The chrome folds, LRC-style: each panel gives its edge to the
// photographs and the tab at that edge brings it back. Tab folds the
// sides, Shift+Tab everything; the choice is remembered across launches.
const PANELS_KEY = 'azimuth.panels';
function loadPanels() {
  const held = recall(PANELS_KEY, {});
  return { left: held.left !== false, right: held.right !== false, top: held.top !== false };
}
function setPanels(panels, { keep = true } = {}) {
  update({ panels });
  if (keep) remember(PANELS_KEY, panels);
  // The drop hangs off the box; a box that folds away takes it along.
  if (!panels.top) searchCards?.close();
}
function togglePanel(side) {
  const panels = { ...read().panels, [side]: !read().panels[side] };
  setPanels(panels);
}
let panelsBeforeFull = null;

// The photographs beside this one, already decoded, so an arrow is one
// frame — the same warming Rank's stage uses. A small held set keeps the
// decoded bitmaps referenced; the oldest fall off the far end.
const warmed = new Map();
function warmOne(photo) {
  const source = photo && (photo.loupe || photo.tile);
  if (!source || warmed.has(source)) return;
  const held = new Image();
  held.decoding = 'async';
  held.src = source;
  held.decode?.().catch(() => {});
  warmed.set(source, held);
  if (warmed.size > 14) warmed.delete(warmed.keys().next().value);
}
function warmNeighbours(index) {
  for (const offset of [1, -1, 2, -2, 3, -3]) warmOne(read().photos.get(index + offset));
}

// The filmstrip: this neighbourhood, the current photograph held centred.
// The window slides only when the current photograph nears an edge, so an
// arrow inside it re-marks two cells and glides — it does not rebuild
// twenty-nine. The rows themselves rebuild only when their answer differs,
// so the two-second pulse cannot make the strip flicker mid-browse.
let stripKey = '';
let stripStart = null;
function renderStrip(state) {
  if (!loupeOpen() || loupe.classList.contains('is-full')) return;
  const current = state.selectedIndex ?? 0;
  // As many cells as the band holds plus a few either side; a 4K band is
  // not a 29-cell island on the left.
  const span = Math.max(15, Math.ceil(loupeStrip.clientWidth / 73) + 6);
  const quarter = Math.floor(span / 4);
  if (stripStart === null || current < stripStart + quarter || current >= stripStart + span - quarter) {
    stripStart = Math.max(0, Math.min(current - Math.floor(span / 2), state.total - span));
  }
  const start = stripStart;
  const end = Math.min(state.total, start + span);
  void pages.ensureRange(start, end);
  let signature = `${start}:${end}`;
  for (let index = start; index < end; index += 1) {
    const photo = state.photos.get(index);
    // The tile's tail rides the signature: an edit changes the URL, not
    // its truthiness, and a strip that only checks presence keeps showing
    // the look the photograph no longer wears.
    signature += photo ? `|${photo.id}.${photo.tile ? photo.tile.slice(-12) : 0}.${photo.status}.${photo.rotate}` : '|·';
  }
  const rebuilt = signature !== stripKey;
  if (rebuilt) {
    stripKey = signature;
    const cells = [];
    for (let index = start; index < end; index += 1) {
      const photo = state.photos.get(index);
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'strip-cell' + (photo?.status === 'picked' ? ' is-picked' : '');
      cell.dataset.index = index;
      cell.setAttribute('role', 'option');
      cell.tabIndex = index === current ? 0 : -1;
      cell.dataset.turn = photo?.rotate || 0;
      cell.setAttribute('aria-label', photo?.tail || `Photo ${index + 1}`);
      if (photo?.tile) {
        const tile = document.createElement('img');
        tile.src = photo.tile;
        tile.alt = '';
        tile.decoding = 'async';
        cell.append(tile);
      }
      cells.push(cell);
    }
    const hadFocus = loupeStrip.contains(document.activeElement);
    loupeStrip.replaceChildren(...cells);
    // A rebuild under the keyboard keeps the keyboard: focus lands on the
    // current cell instead of falling to the body.
    if (hadFocus) loupeStrip.querySelector(`[data-index="${current}"]`)?.focus({ preventScroll: true });
  }
  const held = loupeStrip.querySelector('.is-current');
  const moved = Number(held?.dataset.index) !== current;
  if (moved && held) {
    held.classList.remove('is-current');
    held.tabIndex = -1;
    held.setAttribute('aria-selected', 'false');
  }
  const cell = loupeStrip.querySelector(`[data-index="${current}"]`);
  if (cell && (rebuilt || moved)) {
    // Centring only when the strip was rebuilt or the cursor moved: every
    // other render leaves a hand-scrolled strip where the hand put it.
    cell.classList.add('is-current');
    cell.tabIndex = 0;
    cell.setAttribute('aria-selected', 'true');
    // The strip stays where it is while the current frame is in view; it
    // recentres only when the frame has left the band (or the strip is new).
    const left = cell.offsetLeft - loupeStrip.scrollLeft;
    const outside = left < 0 || left + cell.offsetWidth > loupeStrip.clientWidth;
    if (rebuilt || outside) {
      loupeStrip.scrollTo({
        left: cell.offsetLeft - (loupeStrip.clientWidth - cell.offsetWidth) / 2,
        behavior: rebuilt || matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      });
    }
  }
}

function toggleFull(on = !loupe.classList.contains('is-full')) {
  // The clean room: the strip and every panel leave together, and what was
  // folded before F is put back exactly when F ends.
  loupe.classList.toggle('is-full', on);
  if (on) {
    panelsBeforeFull = { ...read().panels };
    setPanels({ left: false, right: false, top: false }, { keep: false });
  } else {
    setPanels(panelsBeforeFull || loadPanels(), { keep: false });
    panelsBeforeFull = null;
    stripKey = '';
    stripStart = null;
    renderStrip(read());
  }
  loupeView.refresh();
}
const status = document.querySelector('[data-status]');
const stopImport = document.createElement('button');
stopImport.type = 'button';
stopImport.className = 'quiet-button status-stop';
stopImport.dataset.action = 'stop-import';
stopImport.textContent = 'Stop';
stopImport.hidden = true;
status.after(stopImport);

function notify(message) {
  // Every transient message rides the one toast, which floats over any
  // chrome — the sidebar status line stays the ambient truth, so folding
  // the panels never hides what the app just said.
  if (message) undo.show(message);
  else undo.hide();
}
const driveList = document.querySelector('[data-drive-list]');
let drivesSeen = '';
let viewShown = null;
// One polite live region: a view's name on change, a cull's outcome, a
// round's result. Written after a beat so the same sentence twice is heard.
const saidTo = document.querySelector('[data-said]');
function say(sentence) {
  saidTo.textContent = '';
  requestAnimationFrame(() => { saidTo.textContent = sentence; });
}
let loadingSince = null;

const DENSITY_KEY = 'azimuth.row-height';
const SORT_KEY = 'azimuth.sort';
// The remembered density, else one seeded from the window: six across at
// the default, whatever the screen — 150 on a 13-inch, 320 on a 4K.
let rowHeight = recall(DENSITY_KEY, Math.max(150, Math.min(320, Math.round((window.innerWidth - 508) / 6))));
let scrollFrame = null;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// What the window is looking at, as the bridge speaks it. Null when it is
// the whole library, so the server sees "no view" rather than three empties.
function viewOf() {
  const { folders, album, chips, collapsed, expanded, folded, survey } = read();
  const exceptions = [...((collapsed ? expanded : folded) || [])];
  if (!(folders || []).length && !album && !(chips || []).length && !exceptions.length && !collapsed && !survey) return null;
  const view = { folders, album, chips };
  // A survey: Rank draws from the marked photographs and nothing else.
  if (survey) view.ids = survey;
  if (collapsed) view.collapsed = true;
  if (exceptions.length) view[collapsed ? 'expanded' : 'folded'] = exceptions;
  return view;
}

// Open or close one stack in place: its members take their seats after the
// cover and the rest of the grid stays where it is. Which set the cover
// joins depends on the resting state: an exception to collapsed, or to open.
function stackIsOpen(coverId) {
  const { collapsed, expanded, folded } = read();
  return collapsed ? expanded.has(coverId) : !folded.has(coverId);
}
async function toggleStack(coverId) {
  const key = read().collapsed ? 'expanded' : 'folded';
  const held = new Set(read()[key]);
  if (held.has(coverId)) held.delete(coverId);
  else held.add(coverId);
  update({ [key]: held });
  try {
    // The view's size moved -- members came or went -- so this is a full
    // re-read, not a worker tick's.
    await refreshInPlace();
    const { view, sort } = read();
    if (view === 'library' && (sort === 'newest' || sort === 'oldest') && !seeking()) {
      update({ days: await product.days(viewOf()) });
    }
  } catch (error) {
    notify(why(error));
  }
}

// Which word the view is refining, if any: exactly one label chip, or a
// typed query that is not yet a label. The first answer is what creates
// the word — vocabulary is born by teaching.
function teachable() {
  const { chips, query, view } = read();
  if (view !== 'library') return null;
  const worn = (chips || []).filter((c) => c.is === 'label' && !c.not && c.values.length === 1);
  if (worn.length === 1 && !query) return worn[0].values[0];
  // A typed query teaches only when it is already a word the library has
  // been taught; a search phrase must not become a label by accident.
  if (query && !(chips || []).some((c) => c.is === 'label')
      && (read().labels || []).some((l) => l.term === query)) return query;
  return null;
}

async function teach(word, yes) {
  const ids = selection();
  if (!ids.length) return;
  try {
    // teach() returns after the word's answer has recomputed, so the
    // refresh right behind it is authoritative: an excluded photograph
    // leaves the word's view as the key lands, not on the lane's rhythm.
    await product.teach(word, ids, yes);
    const n = ids.length === 1 ? 'This photograph' : `${ids.length.toLocaleString()} photographs`;
    notify(yes ? `${n} taught as “${word}”.` : `${n} taught as not “${word}”.`);
    void labelsPanel.refresh();
    await refreshInPlace();
  } catch (error) {
    notify(why(error));
  }
}

// What a verb acts on: the marked set when there is one, else the focused
// photograph. One answer for cull, Quick, menus and drags.
function selection() {
  const { marked, selected } = read();
  if (marked && marked.size) return [...marked];
  return selected ? [selected.id] : [];
}
let anchorIndex = null;
// Where an arrow run is heading while its row loads; null when it landed.
let cursorAt = null;
// A search is words in the box or a selection asked alike — one door.
const seeking = () => Boolean(read().query || (read().like || []).length);
const asked = () => ({ query: read().query, like: read().like || [] });
const pages = new PageCache({
  pageSize: PAGE,
  load: (offset, limit) => read().view === 'trash'
    ? product.trashPhotos({ limit, offset })
    : seeking()
      ? product.find({ ...asked(), limit, offset, view: viewOf() }).then((answer) => answer.photos)
      : product.photos({ sort: read().sort, limit, offset, view: viewOf() }),
  onPage: (photos, total) => update({ photos, total }),
  onError: (error) => {
    notify(`Some photographs could not be loaded. ${why(error)}`);
  },
});
const undo = createUndo();
const trashWorkflow = createTrashWorkflow({
  product,
  read,
  reload: () => loadView(),
  notify,
  undo,
  selection,
});
const intakeWorkflow = createIntakeWorkflow({
  offer: (message, act, label) => undo.show(message, act, label),
  product,
  notify,
  // Import is its own workspace: entering and leaving it is a view change
  // like any other, and the chrome follows.
  enter: () => { if (read().view !== 'import') update({ view: 'import' }); },
  leave: () => { if (read().view === 'import') update({ view: 'library' }); },
  isShown: () => read().view === 'import',
  progressed: (text) => update({ importing: text || '' }),
  afterImport: async ({ brought = 0 } = {}) => {
    // What just came in is what the person wants to see: Recently added.
    // Nothing came in (stopped, failed): the view stays where it was.
    if (brought > 0) {
      update({ view: 'library', folders: [], album: null, sort: 'added' });
      document.querySelector('[data-sort]').value = 'added';
    }
    await Promise.all([loadView(), loadFolders(), albumsPanel.refresh()]);
  },
});
const albumsPanel = createAlbumsPanel({
  product,
  read,
  update,
  undo,
  notify,
  reload: () => loadView(),
  moved: () => viewMoved(),
  selection,
  describe: (state) => filterBar.describe(state),
  viewOf,
});
const filterBar = createFilterBar({
  undo,
  product,
  read,
  update,
  onChange: () => viewMoved(),
});
// A person's row is a place to go: the view becomes just them. Labels get
// the same landing through their own chip field.
function browseChip(chip) {
  searchBox.value = '';
  if (read().view === 'rank') update({ folders: [], album: null, chips: [chip], query: '', like: [] });
  else update({ view: 'library', folders: [], album: null, chips: [chip], query: '', like: [],
                selected: null, selectedIndex: null });
  viewMoved();
}
const labelsPanel = createLabelsPanel({
  product,
  update,
  notify,
  undo,
  ask: (title, anchor, initial, options) => albumsPanel.ask(title, anchor, initial, options),
  browse: (term) => browseChip({ is: 'label', values: [term] }),
});
const editPanel = createEditPanel({
  product,
  notify,
  undo,
  shown: () => renderChrome(read()),
  preview: (uri) => {
    // The look rides over the rendition without touching the loupe's
    // source memory, so passive renders skip it and the look survives;
    // applied() clears that memory once, which is what lets the real
    // rendition land after a commit or a reset.
    loupeImage.src = uri;
  },
  applied: async (photoId) => {
    // A committed change always deserves the real rendition, even when
    // its URL matches what the loupe believes it has — a look may be
    // sitting over it.
    loupeImage.dataset.source = '';
    await refreshInPlace();
    const { selected, photos } = read();
    if (selected?.id === photoId) {
      for (const [index, photo] of photos) {
        if (photo.id === photoId) {
          update({ selected: { ...selected, ...photo }, selectedIndex: index });
          if (loupeOpen()) showPhoto(read().selected);
          break;
        }
      }
    }
  },
});
const cropSurface = createCropSurface({
  product,
  notify,
  undo,
  applied: async (photoId) => {
    // The verb published the cropped tiles before returning; re-reading the
    // window is all it takes for the new look to be everywhere.
    await refreshInPlace();
    const { selected, photos } = read();
    if (selected?.id === photoId) {
      for (const [index, photo] of photos) {
        if (photo.id === photoId) {
          update({ selected: { ...selected, ...photo }, selectedIndex: index });
          if (loupeOpen()) showPhoto(read().selected);
          break;
        }
      }
    }
  },
});
const peoplePanel = createPeoplePanel({
  product,
  read,
  update,
  notify,
  undo,
  browse: (term) => browseChip({ is: 'person', values: [term] }),
  renamed: () => Promise.all([albumsPanel.refresh(), peoplePanel.refresh()]),
  ask: (title, anchor, initial, options) => albumsPanel.ask(title, anchor, initial, options),
});
// Where the loupe returns to on Esc when it was opened from somewhere
// other than the grid: a look at one card mid-round goes back to the round.
let loupeReturnsTo = null;
const rankWorkflow = createRankWorkflow({
  product,
  read,
  update,
  notify,
  say,
  undo,
  // The cull workflow is made after this one; the stage calls it at key time.
  cull: (verb, options) => cullWorkflow.apply(verb, options),
  viewOf,
  describe: () => filterBar.describe(read()),
  onLook: (photo) => {
    loupeReturnsTo = 'rank';
    update({ selected: photo, selectedIndex: null });
    showPhoto(photo);
    toggleFull(true);
  },
  onLeave: async () => {
    // Rounds moved the ranking, and the view may have moved under the
    // sitting; the grid comes back re-read either way. A survey ends with
    // the sitting.
    if (read().survey) update({ survey: null });
    await loadView();
  },
});

let albumsTimer = null;
function refreshAlbumsSoon() {
  // Smart-album counts follow a cull within a beat, not on the next import.
  clearTimeout(albumsTimer);
  albumsTimer = setTimeout(() => albumsPanel.refresh(), 1200);
}

const cullWorkflow = createCullWorkflow({
  product,
  read,
  say,
  reload: () => loadView(),
  selection,
  patch: (changed) => {
    // The reply is the patch: each change names an identity, a row column
    // (its family) and the value after. Every loaded row of that identity
    // takes it, and so does the focused photograph the loupe is showing.
    const byHash = new Map(changed.map((change) => [change.subject, change]));
    pages.patch((item) => byHash.has(item.hash), (item) => {
      const change = byHash.get(item.hash);
      return { ...item, [change.family]: change.after };
    });
    const selected = read().selected;
    const change = selected && byHash.get(selected.hash);
    if (change) update({ selected: { ...selected, [change.family]: change.after } });
    refreshAlbumsSoon();
  },
  removed: async (changed, selectedIndex) => {
    // Rows leave the view: the positions this window knows are spliced out
    // in place, so the loop never waits on a reload and the scroll never
    // moves. Rows it cannot see (a duplicate identity filed elsewhere) mean
    // the loaded pages are re-read where they stand.
    const going = new Set(changed.map((change) => change.subject));
    const positions = [...read().photos]
      .filter(([, photo]) => going.has(photo.hash))
      .map(([index]) => index)
      .sort((a, b) => b - a);
    const moved = changed.reduce((total, change) => total + change.photos, 0);
    if (positions.length === moved) {
      for (const position of positions) pages.remove(position);
      const counts = read().counts;
      update({
        counts: { ...counts, photos: Math.max(0, counts.photos - moved), trash: counts.trash + moved },
        marked: new Set(),
      });
    } else {
      await refreshInPlace();
      update({ marked: new Set() });
    }
    refreshAlbumsSoon();
    await selectIndex(Math.min(selectedIndex ?? 0, Math.max(0, read().total - 1)));
  },
  selectIndex,
  notify,
  undo,
});

const searchBox = document.querySelector('[data-search]');
function runSearch(text) {
  const query = text.trim();
  if (query === read().query && !(read().like || []).length) return;
  update({ view: 'library', query, like: [] });
  workspace.scrollTo({ top: 0 });
  loadView();
}
// More like this: the same search door, asked with photographs. It asks
// inside where you are -- the folder, the album, the chips stay -- with
// the seeds themselves kept out of the answer.
function moreLikeThis(ids) {
  if (!ids.length) return;
  if (loupeOpen()) closeLoupe();
  searchBox.value = '';
  update({ view: 'library', query: '', like: ids, selected: null, selectedIndex: null });
  workspace.scrollTo({ top: 0 });
  loadView();
}
document.querySelector('[data-like-clear]').addEventListener('click', () => {
  document.querySelector('[data-like-pop]').hidden = true;
  update({ like: [] });
  loadView();
});
// The seeds, on request: the photographs the search is like, from the
// rows in hand or asked for one by one, as a strip under the pill.
document.querySelector('[data-like-seeds]').addEventListener('click', async (event) => {
  const pop = document.querySelector('[data-like-pop]');
  if (!pop.hidden) { pop.hidden = true; return; }
  const ids = read().like || [];
  const held = new Map([...read().photos.values()].map((p) => [p.id, p]));
  const seeds = await Promise.all(ids.slice(0, 12).map(async (id) => held.get(id) || product.photo(id).catch(() => null)));
  pop.replaceChildren(...seeds.filter(Boolean).map((seed) => {
    const tile = document.createElement('img');
    tile.src = seed.tile || '';
    tile.alt = seed.tail || '';
    tile.title = (seed.tail || '').split('/').pop();
    tile.dataset.turn = seed.rotate || 0;
    return tile;
  }));
  if (ids.length > 12) pop.append(Object.assign(document.createElement('span'), { className: 'like-more', textContent: `+${ids.length - 12}` }));
  const at = event.currentTarget.getBoundingClientRect();
  pop.style.left = `${at.left}px`;
  pop.style.top = `${at.bottom + 6}px`;
  pop.hidden = false;
});
document.addEventListener('click', (event) => {
  const pop = document.querySelector('[data-like-pop]');
  if (!pop.hidden && !pop.contains(event.target) && !event.target.closest('[data-like-seeds]')) pop.hidden = true;
});
// The box belongs to the cards: they offer the library's shape on focus,
// narrow it as you type, and leave Enter meaning what it always meant.
// Narrowing by one fact, from a card or a fact in the inspector: the chip
// joins what is worn. It replaces typed words, but narrows a like-search:
// chips ride into the search's view scope, so "similar, and portrait"
// composes.
function applyChip(chip) {
  if (read().query) {
    searchBox.value = '';
    update({ query: '' });
  }
  const held = read().chips || [];
  const same = JSON.stringify(chip);
  if (!held.some((c) => JSON.stringify(c) === same)) {
    update({ view: 'library', chips: [...held, chip] });
  } else if (read().view !== 'library' && read().view !== 'rank') {
    update({ view: 'library' });
  }
  viewMoved();
}
const searchCards = createSearchCards({
  product,
  read,
  update,
  box: searchBox,
  search: runSearch,
  applyChip,
  commands,
});

let lookTimer = null;
let looking = '';
function lookAt(ids) {
  // Tell the library what is on screen, once per settled viewport, so the
  // worker makes these tiles first. The last statement wins; nothing waits.
  const now = ids.join(',');
  if (now === looking) return;
  looking = now;
  clearTimeout(lookTimer);
  lookTimer = setTimeout(() => { product.look(ids).catch(() => {}); }, 120);
}

function visibleGrid() {
  const inTrash = read().view === 'trash';
  const searching = seeking();
  const scanning = read().scanning;
  // An empty album is an invitation, not an empty library — it must never
  // say "Add a folder" inside a place made for gathering.
  const shelf = read().album && (read().albums || []).find((a) => a.id === read().album);
  const inAlbum = Boolean(shelf) && !searching && !scanning && read().view === 'library';
  const inFolder = !inAlbum && !searching && !scanning && (read().folders || []).length > 0;
  const narrowed = !inAlbum && !searching && !scanning && !inFolder && (read().chips || []).length > 0;
  // The empty state says where you are: "Add a folder" is only for a
  // library with nothing in it, never for a place or a filter that is empty.
  const bare = !inTrash && !scanning && !inAlbum && !searching && !inFolder && !narrowed;
  library.renderGrid(grid, read(), {
    emptyTitle: inTrash ? 'Trash is empty.'
      : scanning ? SAYING.finding
        : inAlbum ? 'Nothing in this album yet.'
          : searching ? 'Nothing matches.'
            : inFolder ? (read().folders.length === 1 ? 'Nothing in this folder yet.' : 'Nothing in these folders yet.')
              : narrowed ? 'Nothing matches these filters.' : 'No photographs here yet.',
    emptyCopy: inTrash
      ? 'Rejected photographs stay recoverable here until you empty Trash.'
      : scanning
        ? 'They appear here as they are found.'
        : inAlbum
          ? (shelf.smart
            ? 'No photographs match its filters yet — they join as they qualify.'
            : 'Drag photos onto its name, or right-click any photo anywhere in the library.')
          : searching
            ? 'Try fewer words, or a different idea — meaning works too, not just names.'
            : inFolder
              ? 'Photographs appear as the folder is read. If it is empty on disk, there is nothing to show.'
              : narrowed
                ? 'Loosen a chip, or take one off with its ×.'
                : 'Add a folder to start your library. Azimuth reads it in place and never moves or alters your photographs.',
    emptyAction: bare ? { label: 'Add folder…', run: openDriveDialog } : null,
    select: selectPhoto,
    open: openPhoto,
    stack: (photo) => { void toggleStack(photo.id); },
    stackOpen: stackIsOpen,
    markRange: (start, count, { extend = false } = {}) => { void markRange(start, count, extend); },
    drag: (index, event) => {
      const photo = read().photos.get(index);
      if (!photo) return;
      let ids;
      if (!selection().includes(photo.id)) {
        void selectPhoto(index);
        ids = [photo.id];
      } else {
        ids = selection();
      }
      event.dataTransfer.setData(IDS, JSON.stringify(ids));
      event.dataTransfer.effectAllowed = 'copy';
      if (ids.length > 1) {
        // The ghost says how many ride along, not just the cell under the cursor.
        const badge = document.createElement('div');
        badge.className = 'drag-badge';
        badge.textContent = `${ids.length} photographs`;
        document.body.append(badge);
        event.dataTransfer.setDragImage(badge, 18, 18);
        requestAnimationFrame(() => badge.remove());
      }
    },
    look: lookAt,
    need: (start, end) => pages.ensureRange(start, end),
    rowHeight,
    scrollTop: workspace.scrollTop,
    scrollTo: (top) => { workspace.scrollTop = top; },
    viewportHeight: Math.max(1, workspace.clientHeight - CONTEXTBAR_HEIGHT),
  });
}

function scheduleGrid() {
  if (scrollFrame !== null) return;
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null;
    visibleGrid();
    // The rail is keyed on its own height, so this is free when nothing
    // moved and right after a resize.
    timeline.render(read());
  });
}

// The first run: a home is chosen once and remembered. The dialog cannot be
// dismissed, because nothing works without one; Change… opens the native
// chooser, and the proposal is the fixed local disk with the most room.
async function chooseHome() {
  const submit = homeForm.querySelector('[type="submit"]');
  submit.disabled = true;
  homeDialog.showModal();
  // Nothing takes Enter until there is a proposal to accept: the dialog
  // itself holds the focus, then the submit does.
  homeDialog.focus();
  try {
    homePath.textContent = await product.proposeHome();
    submit.disabled = !homePath.textContent;
    if (!submit.disabled) submit.focus();
  } catch (error) {
    homePath.textContent = '';
    homeError.textContent = error.message;
  }
}

homeDialog.addEventListener('cancel', (event) => event.preventDefault());

homeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = homeForm.querySelector('[type="submit"]');
  submit.disabled = true;
  homeError.textContent = '';
  try {
    await product.settleHome(homePath.textContent);
    homeDialog.close();
    await Promise.all([loadView(), loadFolders()]);
  } catch (error) {
    homeError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

async function openDriveDialog() {
  // The native picker is the question; the dialog is about the folder
  // just chosen -- its name, the one checkbox, and the verb.
  if (driveDialog.open) return;
  let root = '';
  try {
    root = await product.chooseFolder();
  } catch (error) {
    notify(why(error));
    return;
  }
  if (!root) return;
  driveDialog.dataset.root = root;
  driveDialog.querySelector('[data-drive-name]').textContent = root.split(/[\\/]/).filter(Boolean).pop() || root;
  driveDialog.querySelector('[data-drive-path]').textContent = root;
  driveDialog.showModal();
  driveForm.querySelector('[type="submit"]').focus();
}

function closeDriveDialog() {
  if (driveDialog.open) driveDialog.close();
  driveForm.reset();
  driveError.textContent = '';
}


function closeLoupe() {
  if (loupe.classList.contains('is-full')) toggleFull(false);
  const back = loupeReturnsTo || 'library';
  loupeReturnsTo = null;
  if (loupeOpen()) update({ view: back, ...(back === 'rank' ? { selected: null } : {}) });
  // The strip keeps its cells: a reopen finds them where they were and
  // rebuilds only what differs, instead of flashing an empty band.
  loupeView.reset();
  loupeImage.removeAttribute('src');
  delete loupeImage.dataset.source;
  loupeImage.alt = '';
  const selectedIndex = read().selectedIndex;
  if (selectedIndex !== null) {
    // The grid slept while the loupe was up; it catches up now, at real
    // width, with the photograph that was being looked at in view.
    scrollIndexIntoView(selectedIndex);
    scheduleGrid();
    requestAnimationFrame(() => grid.querySelector(`[data-index="${selectedIndex}"]`)?.focus());
  }
}

async function loadView() {
  const requestGeneration = pages.reset();
  // A new answer means a new selection: the marked set clears here, in the
  // one place every view change already passes through.
  update({ loading: true, photos: new Map(), total: 0,
           selected: null, selectedIndex: null, marked: new Set() });
  anchorIndex = null;
  try {
    const { sort, view, query } = read();
    const looking = viewOf();
    const key = JSON.stringify(looking);
    const [counts, drives, trashCount, size, page] = await Promise.all([
      product.counts(),
      product.drives(),
      product.trashCount(),
      view === 'trash' || seeking() ? Promise.resolve(0) : product.size(looking),
      view === 'trash'
        ? product.trashPhotos({ limit: PAGE, offset: 0 })
        : seeking()
          ? product.find({ ...asked(), limit: PAGE, offset: 0, view: looking })
          : product.photos({ sort, limit: PAGE, offset: 0, view: looking }),
    ]);
    if (!pages.isCurrent(requestGeneration) || read().view !== view
        || JSON.stringify(viewOf()) !== key || read().query !== query) return;
    const found = view !== 'trash' && seeking();
    const total = view === 'trash' ? trashCount : found ? page.total : size;
    pages.seed(requestGeneration, found ? page.photos : page, total);
    update({ counts: { ...counts, trash: trashCount }, drives, loading: false });
    // The chapters arrive behind the paint: the first page is on screen in
    // milliseconds, the day headers join when their counts land.
    if (view === 'library' && (sort === 'newest' || sort === 'oldest') && !found) {
      product.days(looking).then((days) => {
        if (pages.isCurrent(requestGeneration) && JSON.stringify(viewOf()) === key) update({ days });
      }).catch(() => {});
    } else if (read().days.length) {
      update({ days: [] });
    }
  } catch (error) {
    if (!pages.isCurrent(requestGeneration)) return;
    update({ loading: false });
    notify(why(error));
  }
}

async function refreshInPlace({ shelves = true, count = shelves } = {}) {
  // The library changed under the window -- a sweep admitted photographs or
  // the worker finished one -- so re-read what is on screen without
  // resetting it. A worker tick changes only the tiles, so it re-reads only
  // the pages; the counts, drives, Trash and shelves (albums, people,
  // labels) are asked again when a sweep or a lane rewrite says they moved.
  // A scan in progress grows the count and the pages, nothing else.
  const generation = pages.generation;
  const { view, query } = read();
  const key = JSON.stringify(viewOf());
  const held = read();
  const [counts, drives, trashCount, size, albums, people, labels] = await Promise.all([
    shelves ? product.counts() : held.counts,
    shelves ? product.drives() : held.drives,
    shelves ? product.trashCount() : held.counts.trash,
    view === 'trash' || seeking() ? Promise.resolve(0) : count ? product.size(viewOf()) : held.total,
    shelves ? product.albums() : held.albums,
    shelves ? product.people().catch(() => held.people) : held.people,
    shelves ? product.labels().catch(() => held.labels) : held.labels,
  ]);
  if (!pages.isCurrent(generation) || read().view !== view
      || JSON.stringify(viewOf()) !== key || read().query !== query) return;
  await pages.refresh(view === 'trash' ? trashCount : seeking() ? read().total : size);
  if (!pages.isCurrent(generation)) return;
  update({ counts: { ...counts, trash: trashCount }, drives, albums, people, labels });
  const { selected, photos } = read();
  if (!selected) return;
  for (const [index, photo] of photos) {
    if (photo.id === selected.id) {
      update({ selected: { ...selected, ...photo }, selectedIndex: index });
      return;
    }
  }
}

// What each kind of owed work is called when the sidebar says what the
// worker is doing. The keys are the cache kinds' own names.
// What the line says for each state, in one place: finding is the sweep
// walking disks, reading is the worker opening files.
let cardSaid = '';
const COMES_BACK = (n) => (n === 1
  ? 'Forgotten. It comes back, with its decisions, if the file ever does.'
  : `${n.toLocaleString()} missing photographs forgotten. They come back, with their decisions, if the files ever do.`);
const SAYING = { finding: 'Finding your photographs…' };
const WORKING = {
  identity: 'Identifying photographs',
  metadata: 'Reading photographs',
  grid: 'Making tiles',
  loupe: 'Making tiles',
  embedding: 'Learning what your photographs look like',
  faces: 'Finding faces',
  photostats: 'Measuring light',
  sharpness: 'Measuring focus',
};

async function followLibrary() {
  // The worker identifies, reads and renders in the background. The window
  // learns of it by asking one free question every couple of seconds and
  // re-reading what it holds only when the answer moved.
  let last = null;
  let rate = 0;
  for (;;) {
    await delay(2000);
    let pulse;
    try {
      pulse = await product.pulse();
    } catch {
      continue;
    }
    const moved = last !== null && pulse.done !== last.done;
    const swept = last !== null && pulse.swept !== last.swept;
    const shaped = last !== null && pulse.shaped !== last.shaped;
    // The lane's own count, on the state: what was said optimistically
    // holds until it moves.
    if (last === null || shaped) update({ shaped: pulse.shaped });
    if (last === null || pulse.cards !== last.cards) showCards();
    // The status line: the kind the worker is on, what it counted as left,
    // and the pace, smoothed so two seconds of luck do not make it jump.
    const instant = last === null ? 0 : (pulse.done - last.done) * 30;
    const pace = rate ? Math.round((rate * 2 + instant) / 3) : instant;
    rate = pulse.doing ? pace : 0;
    // The number beside the word is that kind's own debt; the whole debt
    // decides whether the line may say Up to date at all -- a worker idling
    // between kinds for a beat has not caught up.
    const owed = Object.values(pulse.left || {}).reduce((sum, n) => sum + n, 0);
    const left = (pulse.left || {})[pulse.doing] || 0;
    const working = pulse.doing ? { word: WORKING[pulse.doing] || 'Working', left, rate }
      : owed ? { word: 'Catching up', left: owed, rate: 0 } : null;
    if (JSON.stringify(working) !== JSON.stringify(read().working)) update({ working });
    last = pulse;
    if ((moved || swept || shaped) && !read().loading && !read().scanning) {
      try {
        // Worker ticks re-read the window; the shelves re-count only when a
        // sweep or a lane rewrite actually moved what they say.
        await refreshInPlace({ shelves: swept || shaped });
        if (swept) {
          await loadFolders();
          if (read().view === 'library' && (read().sort === 'newest' || read().sort === 'oldest') && !seeking()) {
            product.days(viewOf()).then((days) => update({ days })).catch(() => {});
          }
        }
      } catch (error) {
        notify(why(error));
      }
    }
  }
}

const folderMenu = document.querySelector('[data-folder-menu]');

async function forgetSelected() {
  const { selected, selectedIndex } = read();
  if (!selected || selected.placed) return;
  try {
    const result = await product.forget([selected.id]);
    if (result.forgotten) {
      await loadView();
      selectIndex(Math.min(selectedIndex, Math.max(0, read().total - 1)));
      notify(COMES_BACK(1));
    }
  } catch (error) {
    notify(why(error));
  }
}

// The export dialog: the Lightroom essentials and nothing else — long
// edge, quality, one rename with a chronological sequence. Choices are
// remembered; the native picker is the last question.
const exportDialog = document.querySelector('[data-export-dialog]');
const EXPORT_KEY = 'azimuth.export';
function openExportDialog() {
  const ids = selection();
  if (!ids.length) return;
  let held = {};
  try { held = JSON.parse(localStorage.getItem(EXPORT_KEY) || '{}'); } catch { held = {}; }
  const edge = held.edge ?? 0;
  for (const button of exportDialog.querySelectorAll('[data-export-size] [data-edge]')) {
    button.classList.toggle('is-active', Number(button.dataset.edge) === edge);
  }
  const quality = exportDialog.querySelector('[data-export-quality]');
  quality.value = held.quality ?? 92;
  exportDialog.querySelector('[data-quality-value]').textContent = quality.value;
  exportDialog.querySelector('[data-export-rename]').value = held.rename ?? '';
  exportDialog.querySelector('[data-export-count]').textContent =
    `Export ${ids.length.toLocaleString()} photograph${ids.length === 1 ? '' : 's'}`;
  renameHint();
  exportDialog.showModal();
  exportDialog.querySelector('[type="submit"]').focus();
}
function renameHint() {
  const name = exportDialog.querySelector('[data-export-rename]').value.trim();
  const hint = exportDialog.querySelector('[data-export-hint]');
  hint.hidden = !name;
  if (name) hint.textContent = `${name}-001.jpg, ${name}-002.jpg, … in capture order`;
}
exportDialog.querySelector('[data-export-rename]').addEventListener('input', renameHint);
exportDialog.querySelector('[data-export-quality]').addEventListener('input', (event) => {
  exportDialog.querySelector('[data-quality-value]').textContent = event.target.value;
});
exportDialog.querySelector('[data-export-size]').addEventListener('click', (event) => {
  const button = event.target.closest('[data-edge]');
  if (!button) return;
  for (const other of exportDialog.querySelectorAll('[data-export-size] [data-edge]')) {
    other.classList.toggle('is-active', other === button);
    other.setAttribute('aria-pressed', String(other === button));
  }
});
document.querySelector('[data-export-form]').addEventListener('submit', (event) => {
  event.preventDefault();
  const ids = selection();
  const edge = Number(exportDialog.querySelector('[data-export-size] .is-active')?.dataset.edge || 0);
  const quality = Number(exportDialog.querySelector('[data-export-quality]').value);
  const rename = exportDialog.querySelector('[data-export-rename]').value.trim();
  localStorage.setItem(EXPORT_KEY, JSON.stringify({ edge, quality, rename }));
  exportDialog.close();
  if (!ids.length) return;
  // Work in flight lives on the status line; the toast is for the outcome.
  update({ doing: `Exporting ${ids.length.toLocaleString()} photograph${ids.length === 1 ? '' : 's'}…` });
  product.exportPhotos(ids, quality, edge, rename).then((said) => {
    update({ doing: '' });
    if (!said.chosen) return;
    const parts = [];
    if (said.exported) parts.push(`${said.exported.toLocaleString()} exported`);
    if (said.missing) parts.push(`${said.missing.toLocaleString()} not here`);
    if (said.failed) parts.push(`${said.failed.toLocaleString()} failed`);
    notify(`${parts.join(', ') || 'Nothing exported'} — in ${said.destination}.`);
  }).catch((error) => { update({ doing: '' }); notify(why(error)); });
});

// Forgetting the missing is a large act with no undo, so it is armed: the
// first click counts and says the number on the verb itself, the second
// does it. No modal, no "Are you sure" -- the menu item is the question.
const forgetItem = folderMenu.querySelector('[data-action="forget-missing"]');
function disarmForget() {
  delete forgetItem.dataset.armed;
  forgetItem.classList.remove('is-armed');
  forgetItem.textContent = 'Forget missing photos…';
}
async function forgetMissing(folder) {
  const where = folder || '*';
  try {
    if (forgetItem.dataset.armed !== where) {
      const probe = await product.forgetMissing(folder || '', true);
      if (!probe.forgotten) {
        folderMenu.hidden = true;
        notify('Nothing is missing here.');
        return;
      }
      forgetItem.dataset.armed = where;
      forgetItem.classList.add('is-armed');
      forgetItem.textContent = `Forget ${probe.forgotten.toLocaleString()} missing — click again`;
      return;
    }
    folderMenu.hidden = true;
    disarmForget();
    const result = await product.forgetMissing(folder || '');
    await Promise.all([loadView(), loadFolders()]);
    notify(COMES_BACK(result.forgotten));
  } catch (error) {
    folderMenu.hidden = true;
    disarmForget();
    notify(why(error));
  }
}

async function showCards() {
  // A card that is here is one quiet chip; the follower notices it within
  // seconds and the pulse says how many there are.
  let cards = [];
  try {
    cards = await product.cards();
  } catch {
    cards = [];
  }
  const chip = document.querySelector('[data-action="import-card"]');
  chip.hidden = cards.length === 0;
  if (cards.length) {
    chip.dataset.root = cards[0].root;
    chip.querySelector('[data-card-label]').textContent = cards[0].label;
    chip.title = `Import from ${cards[0].label} (I)`;
    if (cards[0].label !== cardSaid) { cardSaid = cards[0].label; notify(`A card is in: ${cards[0].label}. I imports it.`); }
  }
}

let synchronizing = false;
async function synchronizeFolder(folder) {
  // Lightroom's Synchronize: walk this folder on every drive that is here,
  // now. The following loop does the same for the working drives each minute.
  // The walk can take minutes on an archive drive, so it rides the sticky
  // scanning state — not a notice that expires under it.
  folderMenu.hidden = true;
  if (synchronizing) return;
  synchronizing = true;
  update({ scanning: true });
  try {
    const results = await product.synchronize(folder || '');
    const added = results.reduce((n, r) => n + (r.photos_added || 0), 0);
    const moved = results.reduce((n, r) => n + (r.photos_moved || 0), 0);
    const retired = results.reduce((n, r) => n + (r.copies_retired || 0), 0);
    await Promise.all([refreshInPlace(), loadFolders()]);
    const parts = [];
    if (added) parts.push(`${added.toLocaleString()} added`);
    if (moved) parts.push(`${moved.toLocaleString()} moved`);
    if (retired) parts.push(`${retired.toLocaleString()} no longer there`);
    notify(parts.length ? `Synchronized: ${parts.join(', ')}.` : 'Synchronized — nothing changed.');
  } catch (error) {
    notify(why(error));
  } finally {
    synchronizing = false;
    update({ scanning: false });
  }
}

const driveMenu = document.querySelector('[data-drive-menu]');

document.addEventListener('contextmenu', (event) => {
  const drive = event.target.closest('.drive-row');
  if (drive) {
    const held = read().drives.find((d) => d.uuid === drive.dataset.drive);
    if (!held?.attached) return;
    event.preventDefault();
    driveMenu.dataset.drive = drive.dataset.drive;
    showMenu(driveMenu, event.clientX, event.clientY);
    return;
  }
  const row = event.target.closest('.folder-row');
  const all = event.target.closest('.nav-row[data-action="all-photos"]');
  if (!row && !all) {
    hideMenu(folderMenu);
    hideMenu(driveMenu);
    return;
  }
  event.preventDefault();
  folderMenu.dataset.folder = row ? row.dataset.folder : '';
  disarmForget();
  showMenu(folderMenu, event.clientX, event.clientY);
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('[data-folder-menu]')) hideMenu(folderMenu);
  if (!event.target.closest('[data-drive-menu]')) hideMenu(driveMenu);
});

async function loadFolders() {
  // The tree is every tail's folders with counts and a safety word; it costs
  // about a second on a large library and changes only when a sweep or an
  // import changes tails, so it is read at boot and after a sweep, never on
  // the first-paint path.
  try {
    update({ tree: await product.folders() });
  } catch (error) {
    notify(why(error));
  }
}

// The view moved under whatever stage is up: Rank re-scopes in place, the
// grid starts from the top of the new answer. Folders, albums, All
// photos and the chips all route through here — one rule, one place.
function viewMoved() {
  if (rankWorkflow.isOpen()) {
    void rankWorkflow.reload();
    return;
  }
  workspace.scrollTo({ top: 0 });
  loadView();
}

// Making and unmaking stacks, with the way back: a stack made is undone by
// taking it apart, a stack taken apart is undone by making it again.
async function restack(verb, ids) {
  try {
    const said = verb === 'stack' ? await product.stack(ids) : await product.unstack(ids);
    const members = verb === 'stack' ? (said.cover ? [said.cover, ...said.members] : []) : said.unstacked;
    if (!members.length) { notify(verb === 'stack' ? 'No burst around this frame — mark the frames and press S.' : 'Nothing here is stacked.'); return; }
    await refreshInPlace();
    const n = members.length;
    undo.show(`${verb === 'stack' ? 'Stacked' : 'Unstacked'} ${n.toLocaleString()} photograph${n === 1 ? '' : 's'}.`, async () => {
      if (verb === 'stack') await product.unstack([members[0]]);
      else await product.stack(members);
      await refreshInPlace();
    });
  } catch (error) {
    notify(why(error));
  }
}

let parked = null;
async function unpark() {
  const held = parked;
  parked = null;
  await loadView();
  if (!held || read().view !== 'library') return;
  workspace.scrollTop = held.scrollTop;
  if (held.index !== null) await selectIndex(held.index, { open: false });
}

let folderAnchor = null;
function showFolder(path, { toggle = false, range = false } = {}) {
  // The tree speaks the grid's grammar: click browses one folder, Ctrl
  // adds another — a shoot that spanned two days is their union — and
  // Shift ranges across the rows between. Toggling the last one off is
  // the whole library again.
  if (seeking()) {
    searchBox.value = '';
    update({ query: '', like: [] });
  }
  const held = read().folders || [];
  let next;
  if (range && folderAnchor !== null && folderAnchor !== path) {
    const rows = [...document.querySelectorAll('.folder-row')].map((row) => row.dataset.folder);
    const from = rows.indexOf(folderAnchor);
    const to = rows.indexOf(path);
    next = from < 0 || to < 0 ? [path]
      : rows.slice(Math.min(from, to), Math.max(from, to) + 1);
  } else if (toggle) {
    next = held.includes(path) ? held.filter((f) => f !== path) : [...held, path];
    folderAnchor = path;
  } else {
    next = [path];
    folderAnchor = path;
  }
  if (read().view === 'rank') update({ folders: next, album: null });
  else update({ view: 'library', folders: next, album: null, selected: null, selectedIndex: null });
  viewMoved();
}

async function scanDrive(drive) {
  update({ scanning: true });
  workspace.scrollTo({ top: 0 });
  try {
    let finished = false;
    const scan = product.refresh(drive.uuid).finally(() => { finished = true; });
    await loadView();
    while (!finished) {
      // The grid grows as the walk finds photographs; the shelves are
      // read once, when the walk lands.
      await delay(500);
      await refreshInPlace({ shelves: false, count: true });
    }
    const result = await scan;
    await refreshInPlace();
    await loadFolders();
    notify(result.applied
      ? `${result.photos_added.toLocaleString()} photograph${result.photos_added === 1 ? '' : 's'} added.`
      : result.reason || 'The folder could not be fully read.');
  } catch (error) {
    notify(why(error));
  } finally {
    update({ scanning: false });
  }
}

function selectPhoto(index, modifiers = {}) {
  // The row as it is now; a cell's click never carries a row of its own.
  const photo = read().photos.get(index);
  if (!photo) return;
  const marked = new Set(read().marked);
  let focus = index;
  if (modifiers.shift && anchorIndex !== null) {
    // The range covers what is loaded between the anchor and here; sparse
    // pages contribute what they hold.
    for (let i = Math.min(anchorIndex, index); i <= Math.max(anchorIndex, index); i += 1) {
      const held = read().photos.get(i);
      if (held) marked.add(held.id);
    }
  } else if (modifiers.toggle) {
    if (marked.has(photo.id)) {
      // Taking a photograph out of the set moves the cursor to the nearest
      // one still in it, so the focus ring never sits outside the marks.
      marked.delete(photo.id);
      focus = nearestMarked(index, marked) ?? index;
    } else marked.add(photo.id);
    anchorIndex = index;
  } else {
    marked.clear();
    marked.add(photo.id);
    anchorIndex = index;
  }
  const chosen = read().photos.get(focus) || photo;
  update({ selected: chosen, selectedIndex: focus, marked });
  detailsSoon(chosen);
}

// A chapter's frames, marked: the rows are brought in if they are not, so a
// day is a day whether or not it has scrolled past.
async function markRange(start, count, extend) {
  await pages.ensureRange(start, start + count, { look: false });
  const marked = extend ? new Set(read().marked) : new Set();
  for (let i = start; i < start + count; i += 1) {
    const held = read().photos.get(i);
    if (held) marked.add(held.id);
  }
  const first = read().photos.get(start);
  anchorIndex = start;
  const chosen = first || read().selected;
  update({ marked, selected: chosen, selectedIndex: first ? start : read().selectedIndex });
  if (chosen) detailsSoon(chosen);
}

function nearestMarked(index, marked) {
  const { photos, total } = read();
  for (let step = 1; step < total; step += 1) {
    for (const at of [index - step, index + step]) {
      const held = photos.get(at);
      if (held && marked.has(held.id)) return at;
    }
    if (index - step < 0 && index + step >= total) break;
  }
  return null;
}

// The inspector's facts arrive behind the cursor, once it settles: an
// arrow run does not wait on a read per step.
let detailTimer = null;
function detailsSoon(photo) {
  clearTimeout(detailTimer);
  detailTimer = setTimeout(async () => {
    try {
      const details = await product.photo(photo.id);
      if (read().selected?.id === photo.id) update({ selected: { ...read().selected, ...details } });
    } catch (error) {
      if (read().selected?.id === photo.id) notify(why(error));
    }
  }, 120);
}

function scrollIndexIntoView(index) {
  const place = library.place(index);
  if (!place) return;
  const viewport = Math.max(1, workspace.clientHeight - CONTEXTBAR_HEIGHT);
  if (place.top < workspace.scrollTop) workspace.scrollTop = place.top;
  else if (place.top + place.height > workspace.scrollTop + viewport) {
    workspace.scrollTop = place.top + place.height - viewport;
  }
}

async function selectIndex(index, { open = loupeOpen(), shift = false } = {}) {
  if (read().total === 0) {
    update({ selected: null, selectedIndex: null });
    return;
  }
  const bounded = Math.max(0, Math.min(read().total - 1, index));
  if (!Number.isFinite(bounded)) return;
  // The run keeps its own place: the next arrow counts from here even while
  // the row is still on its way, and the ring, the accent and the verbs all
  // move together only when it has arrived -- never a ring on one cell and
  // a verb on another.
  cursorAt = bounded;
  if (!read().photos.has(bounded)) await pages.ensure(bounded);
  if (cursorAt !== bounded) return;
  cursorAt = null;
  const photo = read().photos.get(bounded);
  if (!photo) return;
  // Behind an open loupe the grid is display: none; laying it out there
  // measures a zero-width column and poisons the cached layout. It catches
  // up once, at real width, when the loupe closes.
  if (!loupeOpen()) scrollIndexIntoView(bounded);
  selectPhoto(bounded, shift ? { shift: true } : {});
  if (!loupeOpen()) {
    scheduleGrid();
    requestAnimationFrame(() => grid.querySelector(`[data-index="${bounded}"]`)?.focus());
  }
  if (open) showPhoto(photo);
}

// The loupe teaches its grammar on its first three opens, then never again.
const TAUGHT_KEY = 'azimuth.loupe-taught';
function teachLoupe() {
  const hint = document.querySelector('[data-loupe-hint]');
  const times = Number(recall(TAUGHT_KEY, 0)) || 0;
  hint.hidden = times >= 3;
  if (times < 3) remember(TAUGHT_KEY, times + 1);
}

function showPhoto(photo) {
  // The loupe shows the best picture the row has: the loupe tile, else the
  // grid tile scaled up while the loupe tile is made first (the library is
  // told this photograph is what is being looked at). When the row changes
  // under an open loupe, `renderLoupe` swaps the picture in.
  lookAt([photo.id]);
  if (!loupeOpen()) update({ view: 'loupe' });
  renderLoupe(photo);
  renderStrip(read());
  const current = read().selectedIndex;
  if (current !== null) {
    warmNeighbours(current);
    // The neighbourhood's rows stay loaded in every mode — F included — so
    // an arrow run never stalls on a page boundary.
    void pages.ensureRange(Math.max(0, current - 14), Math.min(read().total, current + 15));
  }
}

document.querySelector('[data-loupe-note]').setAttribute('role', 'status');
function renderLoupe(photo) {
  const source = photo.loupe || photo.tile || '';
  const note = document.querySelector('[data-loupe-note]');
  const { state, said } = presence(photo, Boolean(source));
  note.textContent = state === 'here' ? '' : said;
  note.hidden = Boolean(source);
  // An <img> with no source still renders its alt text; while the note is
  // the whole message, the img says nothing.
  loupeImage.alt = source ? (photo.tail || 'Selected photo') : '';
  if (source) loupeView.show(photo, source);
  else {
    // Passing a not-yet-made neighbour keeps the mode: a sharpness run
    // survives the gap and the next picture comes up at 100%.
    loupeView.clear();
    loupeImage.removeAttribute('src');
    delete loupeImage.dataset.source;
  }
}

let facing = { id: null, at: -1 };
async function focusFace(photo) {
  const boxes = await product.faces(photo.id).catch(() => []);
  if (!boxes.length) { notify('No face has been read on this photograph.'); return; }
  facing = { id: photo.id, at: facing.id === photo.id ? (facing.at + 1) % boxes.length : 0 };
  const [x, y, w, h] = boxes[facing.at];
  // A breath above the box's centre: the eyes.
  loupeView.focusAt(x + w / 2, y + h * 0.42);
}

function openPhoto(index) {
  if (read().selectedIndex !== index) selectPhoto(index);
  const photo = read().photos.get(index);
  if (photo) { if (!loupeOpen()) teachLoupe(); showPhoto(photo); }
}


let foldersSeen = null;
let foldersKey = '';
function renderFolders(state) {
  // One row per visible node, depth as a CSS variable; a node opens from its
  // disclosure and scopes the grid from its name. Safety is one quiet mark:
  // amber when something under here exists only on the working disk, hollow
  // when the record drive is away and nobody can say, nothing when all is well.
  // A hollow ring says "the record drive is away, so nobody can say"; with no
  // record drive registered at all there is nothing to say per folder.
  const anyRecord = state.drives.some((drive) => drive.is_record);
  // Rebuilt only when its answer would differ — the two-second pulse must
  // not blink hover states or drop a click mid-swap.
  const key = `${state.view}|${(state.folders || []).join('+')}|${[...state.open].join(',')}|${anyRecord}`;
  if (foldersSeen === state.tree && foldersKey === key) return;
  foldersSeen = state.tree;
  foldersKey = key;
  const rows = [];
  const walk = (nodes, depth) => {
    for (const node of nodes) {
      const active = state.view === 'library' && (state.folders || []).includes(node.path);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'folder-row' + (active ? ' is-active' : '');
      row.style.setProperty('--depth', depth);
      row.dataset.folder = node.path;
      row.title = node.path;
      row.setAttribute('role', 'treeitem');
      row.setAttribute('aria-level', String(depth + 1));
      row.setAttribute('aria-selected', String(active));
      // One tab stop for the tree: the active folder, else the first row.
      row.tabIndex = -1;
      const open = state.open.has(node.path);
      if (node.children.length) row.setAttribute('aria-expanded', String(open));
      const disclosure = document.createElement('span');
      disclosure.className = 'disclosure' + (node.children.length ? ' has-children' : '') + (open ? ' is-open' : '');
      disclosure.dataset.toggle = node.path;
      disclosure.append(icon('chevron'));
      const safety = document.createElement('span');
      safety.className = `safety ${node.safety === 'unknown' && !anyRecord ? 'quiet' : node.safety}`;
      safety.title = node.safety === 'at-risk' ? 'Some of these exist only on the working disk'
        : node.safety === 'unknown' && anyRecord ? 'The record drive is away, so nobody can say' : '';
      if (safety.title) { safety.setAttribute('role', 'img'); safety.setAttribute('aria-label', safety.title); }
      else safety.setAttribute('aria-hidden', 'true');
      const name = document.createElement('span');
      name.className = 'folder-name';
      name.textContent = node.name;
      const count = document.createElement('span');
      count.className = 'folder-count';
      count.textContent = node.total_count.toLocaleString();
      row.append(disclosure, safety, name, count);
      rows.push(row);
      if (open && node.children.length) walk(node.children, depth + 1);
    }
  };
  walk(state.tree, 0);
  const tree = document.querySelector('[data-folder-tree]');
  tree.setAttribute('role', 'tree');
  // One tab stop: the row the keyboard was on, else the active folder,
  // else the first; a rebuild (Right opened a node) keeps the cursor's row.
  const standing = tree.contains(document.activeElement) ? document.activeElement.dataset.folder : null;
  tree.replaceChildren(...rows);
  const stop = (standing && rows.find((r) => r.dataset.folder === standing))
    || rows.find((r) => r.classList.contains('is-active')) || rows[0];
  if (stop) stop.tabIndex = 0;
  if (standing && stop) stop.focus({ preventScroll: true });
}

// A folder is where the file is: a drop is refused with the reason.
acceptDrops(document.querySelector('[data-folder-tree]'), '.folder-row', {
  judge: () => 'A folder is where the file is on disk — move the file to move it',
  drop: () => {},
});

// The tree's keys: arrows walk the visible rows, Right opens, Left folds
// or climbs, Home and End are the ends, typing finds a name.
let treeTyped = '';
let treeTypedAt = 0;
document.querySelector('[data-folder-tree]').addEventListener('keydown', (event) => {
  const row = event.target.closest('.folder-row');
  if (!row) return;
  const rows = [...document.querySelectorAll('[data-folder-tree] .folder-row')];
  const at = rows.indexOf(row);
  const go = (next) => {
    if (!next) return;
    for (const r of rows) r.tabIndex = r === next ? 0 : -1;
    next.focus();
  };
  const open = new Set(read().open);
  if (event.key === 'ArrowDown') go(rows[Math.min(rows.length - 1, at + 1)]);
  else if (event.key === 'ArrowUp') go(rows[Math.max(0, at - 1)]);
  else if (event.key === 'Home') go(rows[0]);
  else if (event.key === 'End') go(rows[rows.length - 1]);
  else if (event.key === 'ArrowRight') {
    if (row.getAttribute('aria-expanded') === 'false') { open.add(row.dataset.folder); update({ open }); }
    else if (row.getAttribute('aria-expanded') === 'true') go(rows[at + 1]);
    else return;
  } else if (event.key === 'ArrowLeft') {
    if (row.getAttribute('aria-expanded') === 'true') { open.delete(row.dataset.folder); update({ open }); }
    else {
      const level = Number(row.getAttribute('aria-level'));
      let up = at - 1;
      while (up >= 0 && Number(rows[up].getAttribute('aria-level')) >= level) up -= 1;
      go(rows[up]);
    }
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const now = Date.now();
    treeTyped = now - treeTypedAt < 800 ? treeTyped + event.key.toLowerCase() : event.key.toLowerCase();
    treeTypedAt = now;
    const found = rows.slice(at + 1).concat(rows.slice(0, at + 1))
      .find((r) => r.querySelector('.folder-name').textContent.toLowerCase().startsWith(treeTyped));
    go(found);
  } else return;
  event.preventDefault();
  event.stopPropagation();
});

function renderChrome(state) {
  albumsPanel.render(state);
  peoplePanel.render(state);
  labelsPanel.render(state);
  filterBar.render(state);
  timeline.render(state);
  library.renderInspector(inspector.querySelector('[data-inspector-facts]'), state.selected,
    { marked: state.marked, photos: state.photos, counts: state.counts, drives: state.drives, working: state.working, showFolder, applyChip, notify });
  editPanel.follows(state.view === 'loupe' ? state.selected : null);
  const count = state.counts.photos.toLocaleString();
  // The import workspace's own panel carries its counts; the library's
  // number beside the word Import would be someone else's answer.
  document.querySelector('[data-photo-count]').textContent = state.view === 'import'
    ? ''
    : state.view === 'people'
      ? `${(state.people || []).length.toLocaleString()} people`
      : state.view === 'library' && !seeking() && !(state.folders || []).length && !state.album && !(state.chips || []).length && state.counts.photos > state.total
        ? `${state.total.toLocaleString()} photographs · ${(state.counts.photos - state.total).toLocaleString()} behind covers`
        : `${state.total.toLocaleString()} photographs`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-trash-count]').textContent = state.counts.trash.toLocaleString();
  // The label speaks about the person's photographs, not the app's memory:
  // how many are selected, or nothing — the title already carries the count.
  // A wait under 200 ms shows nothing: the last answer stays on screen and
  // the word appears only for a wait a person would notice.
  const label = document.querySelector('[data-result-label]');
  const marked = state.marked?.size > 1 ? `${state.marked.size.toLocaleString()} selected` : '';
  if (state.loading) {
    if (loadingSince === null) {
      loadingSince = setTimeout(() => {
        if (read().loading) label.textContent = seeking() ? 'Searching…' : 'Loading your library…';
      }, 200);
    }
    if (!label.textContent.endsWith('…')) label.textContent = marked;
  } else {
    clearTimeout(loadingSince);
    loadingSince = null;
    label.textContent = marked;
  }
  // The running import outranks the reading chatter: it is the one thing
  // the person just asked for.
  // Always a sentence, never a blank: the import you asked for, the sweep,
  // the worker's current kind with what is left and how fast, or the calm.
  const working = state.working;
  const away = (state.drives || []).filter((d) => !d.attached);
  // While an import runs the line carries the way out too.
  stopImport.hidden = !state.importing;
  // The word is what is said aloud (a state: what the worker is doing, a
  // drive away, up to date); the pace is the numbers beside it, read by
  // eye and never spoken, so a screen reader is not told the count every
  // two seconds.
  const [wordSaid, pace] = state.home === null
    ? ['Choose where Azimuth should live to begin', '']
    : state.importing || state.doing
      ? [state.importing || state.doing, '']
      : state.scanning
        ? [SAYING.finding, '']
        : working
          ? [working.word, [working.left ? `${working.left.toLocaleString()} left` : null,
                            working.rate ? `${working.rate.toLocaleString()} / min` : null].filter(Boolean).join(' · ')]
          : away.length
            ? [`${away.length === 1 ? `${away[0].label || 'A drive'} away · those photographs are here when it is` : `${away.length} drives away · those photographs are here when they are`}`, '']
            : state.counts.unidentified
              ? ['Catching up', `${state.counts.unidentified.toLocaleString()} left`]
              : ['Up to date', ''];
  const statusWord = status.querySelector('[data-status-word]');
  const statusPace = status.querySelector('[data-status-pace]');
  if (statusWord.textContent !== wordSaid) statusWord.textContent = wordSaid;
  const paced = pace ? ` · ${pace}` : '';
  if (statusPace.textContent !== paced) statusPace.textContent = paced;
  const importButton = document.querySelector('[data-action="import-folder"]');
  const midImport = Boolean(state.importing);
  if ((importButton.dataset.mid === '1') !== midImport) {
    importButton.dataset.mid = midImport ? '1' : '0';
    importButton.textContent = midImport ? 'Importing…' : 'Import…';
    importButton.title = midImport ? 'Back to the import that is running' : TIPS['import-folder'];
  }
  const searching = state.view === 'library' && Boolean(state.query || (state.like || []).length);
  const ranking = state.view === 'rank';
  const holding = state.view === 'loupe';
  const walled = state.view === 'people';
  const intaking = state.view === 'import';
  shell.classList.toggle('hide-left', !state.panels.left);
  shell.classList.toggle('hide-right', !state.panels.right);
  shell.classList.toggle('hide-top', !state.panels.top);
  loupe.hidden = !holding;
  // The strip measures itself to centre the current frame: laid out only
  // once the stage is shown, so the first centring is a real one.
  if (holding && state.selected) {
    renderLoupe(state.selected);
    renderStrip(state);
    if (state.selectedIndex !== null) warmNeighbours(state.selectedIndex);
  }
  grid.hidden = ranking || holding || walled || intaking;
  document.querySelector('[data-import-stage]').hidden = !intaking;
  document.querySelector('[data-import-panel]').hidden = !intaking;
  inspector.querySelector('[data-inspector-facts]').hidden = intaking || editPanel.isOpen();
  document.querySelector('[data-rank]').hidden = !ranking;
  document.querySelector('[data-rank-progress]').hidden = !ranking;
  document.querySelector('[data-rank-size]').hidden = !ranking;
  document.querySelector('[data-rank-mode]').hidden = !ranking;
  document.querySelector('[data-action="rank"]').hidden = state.view !== 'library' || searching;
  document.querySelector('[data-action="leave-rank"]').hidden = !ranking;
  // A view change hands the keyboard to the stage that arrived, so the
  // focus never falls to the body and the change is heard.
  if (state.view !== viewShown) {
    viewShown = state.view;
    say({ library: 'Library', trash: 'Trash', rank: 'Rank', loupe: 'Loupe', people: 'People', import: 'Import' }[state.view] || state.view);
    const stage = ranking ? document.querySelector('[data-rank]')
      : holding ? loupe
        : walled ? document.querySelector('[data-people-stage]')
          : intaking ? null
            : grid;
    if (stage && !stage.contains(document.activeElement)) {
      const first = stage.querySelector('.rank-card.is-selected, .face-card.is-focus, .strip-cell.is-current, .photo-cell.is-selected, .photo-cell');
      (first || stage).focus({ preventScroll: true });
    }
  }
  document.querySelector('[data-result-label]').hidden = ranking || holding || walled || intaking;
  document.querySelector('[data-density]').closest('label').hidden = ranking || holding || walled || intaking;
  // The chips narrow the library view; Trash and the loupe are not places
  // to edit them, so they leave with their + button.
  document.querySelector('[data-chips]').hidden = state.view === 'trash' || holding || walled || intaking;
  // The quiet invitation to refine: visible exactly when Y and N would land.
  const hint = document.querySelector('[data-teach-hint]');
  const word = teachable();
  hint.hidden = !word || !selection().length;
  if (word) hint.textContent = `Refining “${word}” — Y anchors · N excludes`;
  // The likeness search wears a pill where the chips live: it is part of
  // the question being asked, and its ✕ is how the question ends.
  const pill = document.querySelector('[data-like-pill]');
  const alike = (state.like || []).length;
  pill.hidden = !alike || state.view === 'trash' || holding || walled || intaking;
  if (alike) pill.querySelector('[data-like-seeds]').textContent = `≈ More like ${alike === 1 ? 'this photo' : `${alike} photos`}`;
  const sortBox = document.querySelector('[data-sort]');
  sortBox.closest('label').hidden = state.view === 'trash' || ranking || holding || walled || intaking;
  sortBox.disabled = searching;
  sortBox.closest('label').title = searching ? 'A search is ordered by how well each photograph matches' : '';
  // A decision is keyed on identity, and identity arrives shortly after a
  // sweep; until then the photograph cannot take one, so nothing offers to.
  const canCull = (state.view === 'library' || state.view === 'loupe') && selection().length > 0;
  document.querySelector('[data-cull-actions]').hidden = !canCull;
  const many = (state.marked?.size || 0) > 1;
  // One button, one place: it reads Pick or Clear for what is under the
  // cursor, so focus and the pixel keep their meaning across a click.
  renderStacksToggle(state);
  // Offered while a stack is among the rows held, and always while
  // collapsed: the way back must never scroll out of reach.
  stacksToggle.hidden = state.view !== 'library' || holding || (!state.collapsed && stacksToggle.dataset.any !== 'true');
  const pickButton = document.querySelector('[data-action="pick"]');
  const picked = !many && state.selected?.status === 'picked';
  pickButton.replaceChildren(Object.assign(document.createElement('kbd'), { textContent: picked ? 'U' : 'P' }), ` ${picked ? 'Clear' : 'Pick'}`);
  pickButton.dataset.verb = picked ? 'clear' : 'pick';
  document.querySelector('[data-action="forget"]').hidden = !(state.view === 'library' && state.selected && state.selected.placed === 0);
  const restorable = state.view === 'trash' && (state.marked?.size || state.selected);
  const restore = document.querySelector('[data-action="restore"]');
  restore.hidden = !restorable;
  restore.textContent = state.marked?.size > 1 ? `Restore ${state.marked.size}` : 'Restore';
  document.querySelector('[data-action="empty-trash"]').hidden = state.view !== 'trash' || !state.counts.trash;
  document.querySelector('.nav-row[data-action="all-photos"]').classList.toggle('is-active', state.view === 'library' && !(state.folders || []).length);
  document.querySelector('.nav-row[data-action="trash-view"]').classList.toggle('is-active', state.view === 'trash');
  const shelf = (state.albums || []).find((c) => c.id === state.album);
  const leafs = (state.folders || []).map((f) => f.split('/').pop());
  const where = shelf ? shelf.name
    : leafs.length > 2 ? `${leafs[0]} +${leafs.length - 1}`
    : leafs.join(' + ');
  document.querySelector('.view-title strong').textContent = state.view === 'trash'
    ? 'Trash'
    : intaking ? 'Import'
    : walled ? 'People'
    : searching
      ? (state.query
        ? `Results for “${state.query}”${where ? ` in ${where}` : ''}`
        : `More like ${(state.like || []).length === 1 ? 'this photo' : `${(state.like || []).length} photos`}`)
      : (ranking ? 'Rank · ' : '') + (where || 'All photos');
  document.querySelector('[data-action="add-chip"]').hidden = state.view !== 'library' || ranking;
  // Export acts on the selection; without one there is nothing to offer.
  document.querySelector('[data-action="export"]').hidden =
    !['library', 'loupe'].includes(state.view)
    || !((state.marked && state.marked.size) || state.selected);
  document.querySelector('[data-action="save-view"]').hidden =
    state.view !== 'library' || ranking || searching || !viewOf();
  document.querySelector('[data-action="keep-results"]').hidden = !searching;
  renderFolders(state);

  // An empty section is hidden unless the person can fill it, and then it
  // shows the one row that fills it: Folders offers the folder; Drives
  // appears with the first drive, since a drive is what a folder is on.
  document.querySelector('[data-folders-empty]').hidden = state.home === null || state.drives.length > 0;
  // A flat drive has no folders to list; its section waits for one.
  document.querySelector('[data-section="folders"]').hidden = state.drives.length > 0 && !(state.tree || []).length;
  document.querySelector('[data-drives-section]').hidden = state.drives.length === 0;
  const drivesKey = state.drives.map((drive) => `${drive.uuid}.${drive.attached ? 1 : 0}.${drive.label || drive.root}`).join('|');
  if (drivesKey !== drivesSeen) {
    drivesSeen = drivesKey;
    driveList.replaceChildren(...state.drives.map((drive) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row drive-row';
      row.dataset.drive = drive.uuid;
      row.title = `${drive.root}${drive.attached ? '' : ' — away'} — Shift+F10 for its menu`;
      row.setAttribute('aria-label', `${drive.label || drive.root}, ${drive.attached ? 'attached' : 'away'}`);
      const light = document.createElement('span');
      light.className = drive.attached ? 'drive-light is-online' : 'drive-light';
      const name = document.createElement('span');
      name.textContent = drive.label || drive.root;
      row.append(light, name);
      return row;
    }));
  }
}

function render(state) {
  // Chrome first: it is what shows and hides the grid, and a grid laid out
  // while still hidden measures a zero-width column.
  renderChrome(state);
  if (!['rank', 'loupe', 'import'].includes(state.view)) visibleGrid();
}

driveForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = driveForm.querySelector('[type="submit"]');
  submit.disabled = true;
  driveError.textContent = '';
  try {
    const root = driveDialog.dataset.root;
    if (!root) { driveError.textContent = 'No folder chosen.'; return; }
    const drive = await product.attach(root, driveForm.elements.is_record.checked);
    closeDriveDialog();
    await scanDrive(drive);
  } catch (error) {
    driveError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'add-folder') void openDriveDialog();
  if (action === 'change-home') {
    product.chooseFolder().then((chosen) => { if (chosen) homePath.textContent = chosen; }).catch((error) => { homeError.textContent = error.message; });
  }
  const toggle = event.target.closest('[data-toggle]')?.dataset.toggle;
  if (toggle !== undefined) {
    const open = new Set(read().open);
    if (open.has(toggle)) open.delete(toggle);
    else open.add(toggle);
    update({ open });
    return;
  }
  const folderRow = event.target.closest('.folder-row');
  if (folderRow) {
    showFolder(folderRow.dataset.folder, {
      toggle: event.ctrlKey || event.metaKey, range: event.shiftKey });
    return;
  }
  if (action === 'all-photos') {
    searchBox.value = '';
    if (read().view === 'rank') update({ folders: [], album: null, chips: [], query: '', like: [] });
    else {
      // All photos is always the whole library. A peek at Trash parked from
      // the bare library lands back where it was; from anywhere else the
      // reset stands and the park is dropped.
      const back = read().view === 'trash' && parked !== null;
      parked = back ? parked : null;
      update({ view: 'library', folders: [], album: null, chips: [], query: '', like: [],
               selected: null, selectedIndex: null });
      if (back) { void unpark(); return; }
    }
    viewMoved();
  }
  if (action === 'trash-view' && read().view !== 'trash') {
    // A peek at Trash from the bare library remembers where it was; All
    // photos lands back there, cursor and scroll intact.
    const bare = read().view === 'library' && !(read().folders || []).length && !read().album
      && !(read().chips || []).length && !seeking();
    parked = bare ? { scrollTop: workspace.scrollTop, index: read().selectedIndex } : null;
    update({ view: 'trash' });
    workspace.scrollTo({ top: 0 });
    loadView();
  }
  if (action === 'restore') trashWorkflow.restoreSelected();
  if (action === 'forget') forgetSelected();
  if (action === 'synchronize-folder') synchronizeFolder(folderMenu.dataset.folder);
  if (action === 'forget-missing') { forgetMissing(folderMenu.dataset.folder); return; }
  if (action === 'export') openExportDialog();
  if (action === 'close-export') exportDialog.close();
  if (action === 'export-folder') {
    const folder = folderMenu.dataset.folder || '';
    folderMenu.hidden = true;
    update({ doing: 'Saving metadata for Lightroom…' });
    product.exportFolder(folder).then((said) => {
      const parts = [];
      if (said.written) parts.push(`${said.written.toLocaleString()} written`);
      if (said.unchanged) parts.push(`${said.unchanged.toLocaleString()} already current`);
      if (said.missing) parts.push(`${said.missing.toLocaleString()} not here`);
      update({ doing: '' });
      notify(`Metadata for Lightroom: ${parts.join(', ') || 'nothing to write'}.`);
    }).catch((error) => { update({ doing: '' }); notify(why(error)); });
  }
  if (action === 'adopt-track') {
    folderMenu.hidden = true;
    product.adoptTrack().then((said) => {
      if (said.chosen) notify(said.placed ? `${said.placed.toLocaleString()} photographs placed from the track.` : 'The track covers none of your photographs — check the camera clock.');
    }).catch((error) => notify(why(error)));
  }
  if (action === 'rescan-drive') {
    driveMenu.hidden = true;
    const drive = read().drives.find((d) => d.uuid === driveMenu.dataset.drive);
    if (drive) scanDrive(drive);
  }
  if (action === 'import-folder') {
    // While an import runs, Import… is the door back to its details — no
    // folder picker in the way.
    if (intakeWorkflow.running()) intakeWorkflow.open('');
    else product.chooseFolder().then((chosen) => { if (chosen) intakeWorkflow.open(chosen); }).catch((error) => notify(why(error)));
  }
  if (action === 'import-card') {
    const root = document.querySelector('[data-action="import-card"]').dataset.root;
    if (root) intakeWorkflow.open(root, { isCard: true });
  }
  if (action === 'start-import') intakeWorkflow.finish();
  if (action === 'stop-import') intakeWorkflow.stop();
  if (action === 'close-import') intakeWorkflow.close();
  if (action === 'check-new') intakeWorkflow.checkNew();
  if (action === 'check-all') intakeWorkflow.checkAll();
  if (action === 'check-none') intakeWorkflow.checkNone();
  if (action === 'toggle-left') togglePanel('left');
  if (action === 'toggle-right') togglePanel('right');
  if (action === 'toggle-top') togglePanel('top');
  if (action === 'new-album') albumsPanel.create(event.target);
  if (action === 'save-view') albumsPanel.saveView(event.target);
  if (action === 'keep-results') albumsPanel.keepResults(event.target);
  if (action === 'rank') rankWorkflow.open();
  if (action === 'leave-rank') rankWorkflow.close();
  const size = event.target.closest('[data-rank-size] [data-size]')?.dataset.size;
  if (size) rankWorkflow.resize(Number(size));
  const mode = event.target.closest('[data-rank-mode] [data-mode]')?.dataset.mode;
  if (mode) void rankWorkflow.remode(mode);
  if (action === 'pick') cullWorkflow.apply(event.target.closest('[data-action]').dataset.verb || 'pick');
  if (action === 'collapse-stacks') {
    const collapsed = !read().collapsed;
    remember('azimuth.stacks-collapsed', collapsed);
    update({ collapsed, expanded: new Set(), folded: new Set() });
    viewMoved();
  }
  if (action === 'turn') cullWorkflow.apply(event.shiftKey ? 'turnRight' : 'turnLeft');
  if (action === 'reject') cullWorkflow.apply('reject');
  if (action === 'empty-trash') trashWorkflow.openDialog();
  if (action === 'undo-toast') undo.run();
  if (action === 'close-drive') closeDriveDialog();
  if (action === 'close-empty') trashWorkflow.closeDialog();
  if (action === 'close-loupe') closeLoupe();
  if (action === 'search-clear') { searchBox.value = ''; runSearch(''); searchBox.focus(); }
});

document.addEventListener('keydown', (event) => {
  const target = event.target;
  const isTyping = target.matches('input, select, textarea, [contenteditable="true"]');
  // A slider takes the arrows, not the letters: Esc and D still work on it.
  const isSliding = target.matches('input[type="range"]');
  const onControl = Boolean(target.closest('button'));
  // A modal is the first rung, whichever it is: Esc closes it and nothing
  // behind it hears the key. The home dialog alone cannot be dismissed.
  const modal = document.querySelector('dialog[open]');
  if (modal) {
    if (event.key === 'Escape' && modal !== homeDialog) {
      if (modal === driveDialog) closeDriveDialog();
      else if (modal === exportDialog) exportDialog.close();
      else if (modal === keysDialog) keysDialog.close();
      else trashWorkflow.closeDialog();
      event.preventDefault();
    }
    return;
  }
  if (intakeWorkflow.isOpen()) {
    if (event.key === 'Escape' || ((event.key === 'g' || event.key === 'G') && !isTyping && !onControl)) intakeWorkflow.close();
    if (event.key === 'Enter' && !isTyping && !onControl) {
      intakeWorkflow.finish();
      event.preventDefault();
    }
    if (event.key === ' ' && !isTyping && !onControl) {
      intakeWorkflow.toggleSelected();
      event.preventDefault();
    }
    if (!isTyping && intakeWorkflow.key(event)) event.preventDefault();
    return;
  }
  if (cropSurface.isOpen()) {
    if (cropSurface.key(event)) event.preventDefault();
    return;
  }
  if (editPanel.isOpen() && (event.key === 'Escape' || event.key === 'd' || event.key === 'D') && (!isTyping || isSliding)) {
    editPanel.close();
    loupe.focus({ preventScroll: true });
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !isTyping) {
    undo.run();
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && !isTyping
      && ['library', 'trash'].includes(read().view)) {
    // Everything the view holds — the server's whole answer, never just
    // the pages the scroller happened to load.
    event.preventDefault();
    product.identifiers(viewOf(), read().view === 'trash').then((ids) => {
      if (!ids.length) return;
      // The cursor keeps its place, or takes the first photograph on
      // screen, so the keys and the bar have something to act from.
      const home = read().selectedIndex ?? library.indexAt(workspace.scrollTop) ?? 0;
      update({ marked: new Set(ids), selectedIndex: home,
               selected: read().selected ?? read().photos.get(home) ?? null });
    }).catch((error) => notify(why(error)));
    return;
  }
  if (event.key === '?' && !isTyping) {
    keysDialog.showModal();
    event.preventDefault();
    return;
  }
  if (event.key === '/' && !isTyping && ['library', 'trash', 'loupe'].includes(read().view)) {
    if (loupeOpen()) closeLoupe();
    searchBox.focus();
    searchBox.select();
    event.preventDefault();
    return;
  }
  if (event.key.toLowerCase() === 's' && !isTyping && !event.ctrlKey && !event.metaKey
      && read().view === 'library' && selection().length) {
    // S is the stack key, Lightroom's: on a marked set it makes one; on a
    // frame in a stack it opens or folds that stack; on a lone frame it
    // stacks the burst the cadence law finds around it. Shift+S takes a
    // stack apart. Every one has its way back.
    const ids = selection();
    const held = read().selected;
    const cover = held?.stack_of || (held?.stack ? held.id : null);
    if (event.shiftKey) void restack('unstack', cover ? [cover] : ids);
    else if (ids.length > 1) void restack('stack', ids);
    else if (cover) void toggleStack(cover);
    else void restack('stack', ids);
    event.preventDefault();
    return;
  }
  if (event.key === 'Escape') {
    // One rung at a time: a toast, a menu, then the zoom, then the clean
    // room, then the loupe itself — the popovers close in their own earlier
    // handlers.
    if (undo.visible() && undo.pending()) undo.dismiss();
    else if (!folderMenu.hidden || !driveMenu.hidden) {
      hideMenu(folderMenu);
      hideMenu(driveMenu);
    }
    else if (loupeOpen()) {
      // A look from a round goes straight back to it; otherwise one rung.
      if (loupeReturnsTo) closeLoupe();
      else if (!loupeView.escape()) {
        if (loupe.classList.contains('is-full')) toggleFull(false);
        else closeLoupe();
      }
    }
    else if (rankWorkflow.isOpen()) rankWorkflow.close();
    else if (read().view === 'people') update({ view: 'library' });
    else if (document.activeElement === searchBox) {
      // An empty box with nothing left to put away: the keyboard goes back
      // to the photographs, where the next key means something.
      searchBox.blur();
      (grid.querySelector('.photo-cell.is-focus') || grid.querySelector('.photo-cell.is-selected') || grid.querySelector('.photo-cell'))?.focus();
    }
    else if (read().selected || read().marked?.size) {
      // The marks and the selection go; the cursor keeps its place, so the
      // next arrow moves from here and not from the top.
      update({ selected: null, marked: new Set() });
      anchorIndex = null;
    } else if (seeking()) { searchBox.value = ''; runSearch(''); }
    else if (read().chips.length) { update({ chips: [] }); viewMoved(); }
    else if (read().album || (read().folders || []).length) { update({ album: null, folders: [] }); viewMoved(); }
    else if (read().view === 'trash') document.querySelector('[data-action="all-photos"]').click();
    else return;
    event.preventDefault();
    return;
  }
  if (isTyping) return;
  // A focused control keeps its own keys: a letter, Enter, Space or an
  // arrow on a button is that button's, never a photograph's verb. The
  // app's chords (Ctrl, Alt) and the two chrome keys (F6, Tab) pass.
  // The wall's Y is the one letter that acts from a focused card: it
  // answers the question at the top, wherever the cursor is.
  if (read().view === 'people' && (event.key === 'y' || event.key === 'Y') && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (peoplePanel.key(event)) event.preventDefault();
    return;
  }
  if (onControl && !event.ctrlKey && !event.metaKey && !event.altKey
      && (event.key.length === 1 || ['Enter', ' ', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key))
      && event.key !== '?' && event.key !== '/') return;
  // K1: F6 walks the chrome — bar, sidebar, inspector, topbar — from
  // wherever the keyboard is; Shift+F6 walks it backwards.
  if (event.key === 'F6') {
    const regions = ['.contextbar', '.sidebar', '.inspector', '.topbar']
      .map((s) => document.querySelector(s)).filter((r) => r && !r.hidden && r.offsetParent !== null);
    const at = regions.findIndex((r) => r.contains(document.activeElement));
    const step = event.shiftKey ? -1 : 1;
    for (let k = 1; k <= regions.length; k += 1) {
      const region = regions[(at + step * k + regions.length * 2) % regions.length];
      const first = region.querySelector('button:not([hidden]):not([disabled]), [tabindex="0"], input:not([hidden]), select:not([hidden])');
      if (first && first.offsetParent !== null) { first.focus({ preventScroll: true }); break; }
    }
    event.preventDefault();
    return;
  }
  if (event.key === 'Tab') {
    // Tab folds the panels only from the photographs or from nowhere: on a
    // chrome control it is Tab, so every button can be reached without a
    // mouse. On an empty library the only thing worth reaching is the one
    // button in the empty state; folding panels there would strand the keyboard.
    const fromChrome = target !== document.body
      && !target.closest('.photo-grid, .rank-stage, .loupe-stage, .face-wall, .import-stage');
    if (fromChrome) return;
    if (read().view === 'import') {
      // The import's controls live in the inspector: Tab goes to them
      // instead of folding them away.
      document.querySelector('[data-import-panel] button:not([hidden]), [data-import-panel] input')?.focus();
      event.preventDefault();
      return;
    }
    if (read().view === 'library' && read().total === 0 && !read().loading) return;
    const panels = read().panels;
    if (event.shiftKey) {
      const any = panels.left || panels.right || panels.top;
      setPanels({ left: !any, right: !any, top: !any });
    } else {
      const sides = panels.left || panels.right;
      setPanels({ ...panels, left: !sides, right: !sides });
    }
    event.preventDefault();
    return;
  }
  if (rankWorkflow.isOpen()) {
    // A focused control (a size or mode button) keeps Enter and Space.
    if (onControl && (event.key === 'Enter' || event.key === ' ')) return;
    if ((event.key === 'g' || event.key === 'G') && !isTyping) { rankWorkflow.close(); event.preventDefault(); return; }
    if (rankWorkflow.key(event)) event.preventDefault();
    return;
  }

  const current = cursorAt ?? read().selectedIndex;
  const key = event.key.toLowerCase();
  // The grid and the loupe by their Lightroom letters: G back to the grid
  // from the loupe, E into the loupe from the grid.
  if (key === 'g' && loupeOpen() && !event.ctrlKey && !event.metaKey) { closeLoupe(); event.preventDefault(); return; }
  if (key === 'e' && read().view === 'library' && read().selectedIndex !== null && !event.ctrlKey && !event.metaKey) {
    openPhoto(read().selectedIndex);
    event.preventDefault();
    return;
  }
  if (key === 'l' && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && !onControl
      && ['library', 'loupe'].includes(read().view)) {
    // Lights out: the chrome goes dark to judge tone; L again brings it back.
    shell.classList.toggle('is-lights-out');
    event.preventDefault();
    return;
  }
  if (key === 'b' && ['library', 'loupe'].includes(read().view) && selection().length) {
    albumsPanel.toss();
    event.preventDefault();
    return;
  }
  if ((key === 'z' || event.key === ' ') && loupeOpen() && !event.ctrlKey && !event.metaKey) {
    loupeView.toggle();
    event.preventDefault();
    return;
  }
  if (key === 'i' && ['library', 'trash'].includes(read().view) && !event.ctrlKey && !event.metaKey) {
    // I brings the card in, or opens the running import's details.
    const chip = document.querySelector('[data-action="import-card"]');
    if (!chip.hidden) intakeWorkflow.open(chip.dataset.root, { isCard: true });
    else if (intakeWorkflow.running()) intakeWorkflow.open('');
    else return;
    event.preventDefault();
    return;
  }
  if (key === 'f' && ['library', 'loupe'].includes(read().view)) {
    if (loupeOpen()) toggleFull();
    else if (read().selected) {
      showPhoto(read().selected);
      toggleFull(true);
    }
    event.preventDefault();
    return;
  }
  // Crop: Lightroom's rectangle, over the loupe. From the grid it opens
  // the loupe on its way.
  if (key === 'c' && ['library', 'loupe'].includes(read().view) && read().selected?.hash) {
    if (!loupeOpen()) showPhoto(read().selected);
    void cropSurface.open(read().selected);
    event.preventDefault();
    return;
  }
  // Develop: Lightroom's D, the Basic panel in the inspector's seat.
  if (key === 'd' && ['library', 'loupe'].includes(read().view) && read().selected?.hash) {
    if (editPanel.isOpen()) editPanel.close();
    else {
      if (!loupeOpen()) showPhoto(read().selected);
      // The panel lives in the inspector; the clean room has none.
      if (loupe.classList.contains('is-full')) toggleFull(false);
      void editPanel.open(read().selected).then(() => editPanel.focus());
    }
    event.preventDefault();
    return;
  }
  if (key === 'u' && read().view === 'trash' && selection().length) {
    trashWorkflow.restoreSelected();
    event.preventDefault();
    return;
  }
  // Refining a label: inside one word's view, Y anchors and N excludes.
  // Deliberately not X — X rejects the photograph; N only teaches the word.
  const word = teachable();
  if (word && (key === 'y' || key === 'n') && selection().length) {
    void teach(word, key === 'y');
    event.preventDefault();
    return;
  }
  // The pass: V cycles what the grid shows — everything, the unflagged
  // (the second pass after a reject pass), the picked (the survivors).
  if (key === 'v' && read().view === 'library' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const held = read().chips || [];
    const worn = held.find((c) => c.is === 'status' && !c.not);
    const next = !worn ? 'unflagged' : worn.values[0] === 'unflagged' ? 'picked' : null;
    const rest = held.filter((c) => c !== worn);
    update({ chips: next ? [...rest, { is: 'status', values: [next] }] : rest });
    viewMoved();
    notify(next === 'unflagged' ? 'The unflagged.' : next === 'picked' ? 'The picked.' : 'Everything.');
    event.preventDefault();
    return;
  }
  // Survey: the marked frames of a burst go to Rank as a round of their
  // own (Lightroom's N), sized to the burst.
  if (key === 'n' && read().view === 'library' && (read().marked?.size || 0) >= 2 && !event.ctrlKey && !event.metaKey) {
    const ids = [...read().marked];
    update({ survey: ids });
    const sizes = [2, 4, 6, 9, 12];
    void rankWorkflow.resize(sizes.find((n) => n >= ids.length) || 12).then(() => rankWorkflow.open());
    event.preventDefault();
    return;
  }
  // Face focus: . puts the first face at 100% under the centre, again the
  // next, and round; a picture with no faces read says so.
  if (key === '.' && loupeOpen() && read().selected && !event.ctrlKey && !event.metaKey) {
    void focusFace(read().selected);
    event.preventDefault();
    return;
  }
  const cullActions = { p: 'pick', u: 'clear', x: 'reject', r: event.shiftKey ? 'turnRight' : 'turnLeft' };
  if (key in cullActions && ['library', 'loupe'].includes(read().view) && selection().length) {
    cullWorkflow.apply(cullActions[key]);
    event.preventDefault();
    return;
  }
  if (read().view === 'people') {
    if (onControl && (event.key === 'Enter' || event.key === ' ')) return;
    if ((event.key === 'g' || event.key === 'G') && !isTyping) { update({ view: 'library' }); event.preventDefault(); return; }
    if (peoplePanel.key(event)) event.preventDefault();
    return;
  }
  const extend = { shift: event.shiftKey };
  if (loupeOpen() && loupeView.zoomed() && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) {
    // Zoomed in, the arrows walk the picture (Shift: half a view); Home
    // recentres. At Fit they are next and previous again.
    if (event.key === 'Home') loupeView.recentre();
    else {
      const part = event.shiftKey ? 0.5 : 0.1;
      const dx = event.key === 'ArrowLeft' ? part : event.key === 'ArrowRight' ? -part : 0;
      const dy = event.key === 'ArrowUp' ? part : event.key === 'ArrowDown' ? -part : 0;
      loupeView.pan(dx, dy);
    }
    event.preventDefault();
    return;
  }
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    const move = event.key === 'ArrowLeft' ? -1 : 1;
    selectIndex((current ?? (move > 0 ? -1 : read().total)) + move, extend);
    event.preventDefault();
  } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    // Rows do not share columns, so up and down mean the nearest cell in the
    // row above or below, which the lens knows from its layout.
    const direction = event.key === 'ArrowUp' ? -1 : 1;
    const next = current === null ? (direction > 0 ? 0 : read().total - 1) : library.neighbour(current, direction);
    if (next !== null) selectIndex(next, extend);
    event.preventDefault();
  } else if (event.key === 'Home') {
    selectIndex(0, extend);
    event.preventDefault();
  } else if (event.key === 'End') {
    selectIndex(read().total - 1, extend);
    event.preventDefault();
  } else if ((event.key === 'Enter' || event.key === ' ') && read().selected) {
    showPhoto(read().selected);
    event.preventDefault();
  }
});

// Every key, once: the tooltips on the buttons and the ? sheet both read
// this table, so a key cannot be documented in one place and not the other.
// The one table of keys. A row may name the button that does the same
// thing; that button's tooltip reads its key from here, so the sheet and
// the tooltips can never disagree.
const SHORTCUTS = [
  ['Everywhere', [
    ['?', 'This sheet'], ['/', 'Search'], ['>', 'In the search box: every verb on the screen'], ['F6', 'The chrome: bar, sidebar, details, top bar'], ['Tab', 'Fold the side panels', ['toggle-left', 'toggle-right']], ['Shift+Tab', 'Fold everything', ['toggle-top']],
    ['Esc', 'Back one step'], ['Ctrl+Z', 'Undo', ['undo-toast']], ['Ctrl+A', 'Select all'], ['L', 'Lights out'],
  ]],
  ['The grid', [
    ['Arrows', 'Move the cursor'], ['Shift+Arrows', 'Extend the selection'], ['Home / End', 'First / last'],
    ['Enter / Space / E', 'Open the loupe'], ['G', 'Back to the grid, from anywhere'], ['N', 'Survey the marked frames in Rank'],
    ['V', 'The pass: everything, the unflagged, the picked'],
    ['P / U', 'Pick / clear the pick', ['pick']], ['X', 'Reject', ['reject']],
    ['R / Shift+R', 'Turn left / right', ['turn']], ['B', 'Toss into the Quick album'], ['S', 'Stack the marked frames, or the burst around this one; open or fold a stack'], ['Shift+S', 'Unstack'],
    ['F', 'The clean room'], ['C', 'Crop'], ['D', 'Develop'], ['I', 'Import the card'], ['Ctrl+Wheel', 'Density'],
  ]],
  ['The mouse', [
    ['Click', 'Select'], ['Shift+Click', 'Extend the selection'], ['Ctrl+Click', 'Add or take out'],
    ['Double-click', 'Open the loupe'], ['A day\u2019s name', 'Select the day'], ['Drag to an album', 'Add'],
  ]],
  ['Filters', [['Backspace, Delete', 'Remove the chip'], ['Esc', 'Close the editor']]],
  ['The rail', [['Arrows', 'A day'], ['PageUp / PageDown', 'A year'], ['Home / End', 'The ends']]],
  ['The loupe', [
    ['Z / Space', 'Fit or 100%'], ['.', 'The next face at 100%'], ['Arrows', 'Next / previous; zoomed in, walk the picture'], ['Shift+Arrows', 'Zoomed in, half a view'], ['Home', 'Zoomed in, the centre'],
    ['F', 'Leave the clean room'], ['Esc', 'Fit, then close', ['close-loupe', 'leave-rank']],
  ]],
  ['Rank', [
    ['1\u20139, 0, -, =', 'Pick that card'], ['Arrows', 'Move, or pick a side of a pair'], ['Enter', 'Pick the selected'],
    ['Z / F', 'Look closer'], ['P / U / X / R', 'The selected card, else the one under the mouse'],
    ['[ / ]', 'Fewer or more at once'], ['M', 'How the set is drawn'],
  ]],
  ['Crop', [['Arrows', 'Nudge 1%'], ['Shift+Arrows', 'Nudge 5%'], ['Alt+Arrows', 'Grow or shrink'], ['0', 'Remove the crop'], ['Enter', 'Apply']]],
  ['Import', [['Arrows', 'Move'], ['Space', 'Check or uncheck'], ['Ctrl+A', 'Select all'], ['Enter', 'Import']]],
  ['Trash', [['U', 'Restore', ['restore']]]],
  ['Folders', [['Arrows', 'Walk; Left and Right fold and open'], ['Home / End', 'First / last'], ['Amber dot', 'Only on the working disk'], ['Hollow ring', 'The record drive is away']]],
  ['Teaching', [['Y / N', 'This is / is not the word']]],
];
const TIPS = {
  'import-folder': 'Import a folder\u2026', export: 'Export the selection\u2026', 'add-folder': 'Add a folder to the library',
  rank: 'Rank the photographs you are looking at', 'leave-rank': 'Back to the grid', forget: 'Forget this missing photograph',
  'add-chip': 'Narrow by a fact', 'save-view': 'Save these filters as an album', 'keep-results': 'Save what the search found',
  restore: 'Put it back in the library', 'empty-trash': 'Delete everything in Trash for good',
  'toggle-left': 'Show or hide the sidebar', 'toggle-right': 'Show or hide the details', 'toggle-top': 'Show or hide the top bar',
  pick: 'Pick, or clear the pick', turn: 'Turn left \u00b7 Shift turns right', reject: 'Reject', 'search-clear': 'Clear the search',
  'undo-toast': 'Take it back', 'close-loupe': 'Close the loupe',
  'all-photos': 'All photographs', 'trash-view': 'Trash', 'new-album': 'New album\u2026', 'collapse-stacks': 'Fold or open every stack',
  'crop-reset': 'Remove the crop', 'crop-cancel': 'Leave the crop as it was', 'crop-apply': 'Apply the crop',
  'edit-reset': 'Reset the edit', 'edit-close': 'Close Develop',
  'import-card': 'Import the card\u2026', 'close-import': 'Cancel the import', 'check-new': 'Check only the new photographs',
  'check-all': 'Check every photograph', 'check-none': 'Uncheck everything', 'start-import': 'Import the checked photographs', 'stop-import': 'Stop the import',
  'change-home': 'Change where the library lives\u2026', 'close-drive': 'Close', 'close-empty': 'Close', 'close-export': 'Close',
  'synchronize-folder': 'Synchronize this folder with the disk', 'forget-missing': 'Forget the missing photographs\u2026',
  'adopt-track': 'Add a GPS track (GPX)\u2026', 'export-folder': 'Save metadata for Lightroom', 'rescan-drive': 'Re-scan this drive now',
  'rename-album': 'Rename the album\u2026', 'freeze-album': 'Freeze into a plain album', 'delete-album': 'Delete the album',
  'rename-label': 'Rename the word\u2026', 'forget-label': 'Forget the word', 'name-ok': 'Save the name',
  'bring-culled': 'Bring the photographs culled before',
};
const KEY_OF = new Map();
for (const [, keys] of SHORTCUTS) for (const [key, , actions] of keys) for (const action of actions || []) if (!KEY_OF.has(action)) KEY_OF.set(action, key);
for (const [action, tip] of Object.entries(TIPS)) {
  const key = KEY_OF.get(action);
  for (const node of document.querySelectorAll(`[data-action="${action}"]`)) if (!node.title) node.title = key ? `${tip} (${key})` : tip;
}
// The command line: what the tooltips say, for every verb that is on the
// screen right now, by the same words. Choosing one presses its button.
const onScreen = (node) => !node.disabled && node.getClientRects().length > 0;
function commands(query) {
  const seen = new Set();
  const found = [];
  const want = query.toLowerCase();
  for (const node of document.querySelectorAll('[data-action]')) {
    const action = node.dataset.action;
    const tip = TIPS[action];
    if (!tip || seen.has(action) || !onScreen(node)) continue;
    if (want && !tip.toLowerCase().includes(want)) continue;
    seen.add(action);
    found.push({
      label: tip.replace(/\u2026$/, ''), count: KEY_OF.get(action), glyph: 'chevron',
      run: () => {
        // The button is found again now, since shelves re-render
        // underneath; then the box empties the way Clear does, so the grid
        // follows it (Clear itself hides once the box is empty).
        const button = [...document.querySelectorAll(`[data-action="${action}"]`)].find(onScreen);
        searchBox.value = '';
        searchBox.dispatchEvent(new Event('input', { bubbles: true }));
        if (button) button.click();
        else notify('That verb has left the screen.');
      },
    });
  }
  return found;
}
const keysDialog = document.querySelector('[data-keys-dialog]');
{
  const body = keysDialog.querySelector('[data-keys-body]');
  for (const [where, keys] of SHORTCUTS) {
    const head = document.createElement('p');
    head.className = 'eyebrow';
    head.textContent = where;
    const list = document.createElement('dl');
    list.className = 'keys';
    for (const [key, does] of keys) {
      const dt = document.createElement('dt');
      // Caps split where the words do: on a slash and on a comma alike.
      dt.replaceChildren(...key.split(/( \/ |, )/).map((part, i) => (i % 2 ? part : Object.assign(document.createElement('kbd'), { textContent: part }))));
      const dd = document.createElement('dd');
      dd.textContent = does;
      list.append(dt, dd);
    }
    body.append(head, list);
  }
}
const stacksToggle = document.querySelector('[data-action="collapse-stacks"]');
function renderStacksToggle(state) {
  // The button names what it would do, and is offered only where there is
  // a stack among the photographs held for this view.
  stacksToggle.replaceChildren(icon('stack'), state.collapsed ? ' Open stacks' : ' Collapse stacks');
  stacksToggle.setAttribute('aria-pressed', String(state.collapsed));
  stacksToggle.title = state.collapsed ? 'Show every frame of every stack' : 'Fold every stack behind its cover';
  let any = false;
  for (const photo of state.photos.values()) {
    if (photo.stack || photo.stack_of) { any = true; break; }
  }
  stacksToggle.dataset.any = String(any);
}
update({ collapsed: recall('azimuth.stacks-collapsed', false) === true });
document.querySelector('[data-sort]').addEventListener('change', async (event) => {
  // The photograph under the cursor is what the person was looking at; a
  // new order finds it again rather than dropping it.
  const held = read().selected;
  update({ sort: event.target.value });
  remember(SORT_KEY, event.target.value);
  workspace.scrollTo({ top: 0 });
  await loadView();
  if (!held || read().view !== 'library' || seeking()) return;
  const at = await product.position(held.id, read().sort, viewOf()).catch(() => null);
  if (at !== null && at >= 0 && read().sort === event.target.value) selectIndex(at, { open: false });
});
{
  const sort = recall(SORT_KEY, 'newest');
  const options = [...document.querySelector('[data-sort]').options].map((o) => o.value);
  if (options.includes(sort)) {
    update({ sort });
    document.querySelector('[data-sort]').value = sort;
  }
}

const density = document.querySelector('[data-density]');
density.value = rowHeight;
density.addEventListener('input', (event) => {
  // The lens anchors the first visible cell across the re-layout itself; a
  // selection, when there is one, is what the person is looking at.
  rowHeight = Number(event.target.value);
  remember(DENSITY_KEY, rowHeight);
  visibleGrid();
  if (read().selectedIndex !== null) {
    scrollIndexIntoView(read().selectedIndex);
    visibleGrid();
  }
});
let wheelFrame = null;
let wheelHeight = null;
workspace.addEventListener('wheel', (event) => {
  // Ctrl+wheel is the grid's own zoom: the same slider, driven from where
  // the eyes already are, continuous with the wheel's travel. It is never
  // the page's zoom, in any view.
  if (!event.ctrlKey) return;
  event.preventDefault();
  if (read().view !== 'library') return;
  wheelHeight = Math.max(Number(density.min), Math.min(Number(density.max),
    (wheelHeight ?? rowHeight) - event.deltaY * 0.15));
  if (wheelFrame !== null) return;
  wheelFrame = requestAnimationFrame(() => {
    wheelFrame = null;
    const next = Math.round(wheelHeight);
    wheelHeight = null;
    if (next === rowHeight) return;
    density.value = next;
    density.dispatchEvent(new Event('input'));
  });
}, { passive: false });

// The same verbs under the mouse as in the bar and on the keys — one list
// for wherever a photograph can be right-clicked.
const photoVerbs = () => (read().view === 'trash' ? [
  { label: 'Restore — U', run: () => trashWorkflow.restoreSelected() },
] : [
  ...CULL_MENU.map(({ action, label }) => ({
    label,
    run: () => cullWorkflow.apply(action),
  })),
  {
    label: 'Crop… — C',
    run: () => {
      const held = read().selected;
      if (!held?.hash) return;
      if (!loupeOpen()) showPhoto(held);
      void cropSurface.open(held);
    },
  },
  { label: 'More like this', run: () => moreLikeThis(selection()) },
  {
    label: 'Save metadata for Lightroom',
    run: async () => {
      try {
        const said = await product.exportSettings(selection());
        const parts = [];
        if (said.written) parts.push(`${said.written.toLocaleString()} written`);
        if (said.unchanged) parts.push(`${said.unchanged.toLocaleString()} already current`);
        if (said.missing) parts.push(`${said.missing.toLocaleString()} not here`);
        notify(`Metadata for Lightroom: ${parts.join(', ') || 'nothing to write'}.`);
      } catch (error) {
        notify(why(error));
      }
    },
  },
]);
grid.addEventListener('contextmenu', (event) => {
  const cell = event.target.closest('.photo-cell[data-kind="photo"]');
  if (!cell || !['library', 'trash'].includes(read().view)) return;
  const index = Number(cell.dataset.index);
  const photo = read().photos.get(index);
  if (photo && !selection().includes(photo.id)) void selectPhoto(index);
  albumsPanel.menuFor(event, photoVerbs());
});
loupe.addEventListener('contextmenu', (event) => {
  if (!read().selected) return;
  albumsPanel.menuFor(event, photoVerbs());
});

workspace.addEventListener('scroll', scheduleGrid, { passive: true });
new ResizeObserver(scheduleGrid).observe(workspace);
loupeStrip.addEventListener('click', (event) => {
  const cell = event.target.closest('.strip-cell');
  if (cell) selectIndex(Number(cell.dataset.index));
  event.stopPropagation();
});
loupeStrip.addEventListener('pointerover', (event) => {
  // Hover precedes a strip click by a beat — long enough to decode the
  // picture it is about to ask for.
  const cell = event.target.closest('.strip-cell');
  if (cell) warmOne(read().photos.get(Number(cell.dataset.index)));
});
loupeStrip.addEventListener('wheel', (event) => {
  loupeStrip.scrollLeft += event.deltaY;
  event.preventDefault();
  event.stopPropagation();
}, { passive: false });

// Sidebar sections fold from their headings, and the folds are remembered —
// a long library keeps only the shelves it is using in view.
const FOLDED_KEY = 'azimuth.folded';
const folded = new Set(recall(FOLDED_KEY, []));
function applyFolds() {
  for (const section of document.querySelectorAll('.sidebar section[data-section]')) {
    const head = section.querySelector('.eyebrow');
    if (head && !head.querySelector('.icon')) head.append(icon('chevron'));
    const shut = folded.has(section.dataset.section);
    section.classList.toggle('is-folded', shut);
    head?.setAttribute('aria-expanded', String(!shut));
  }
}
document.querySelector('.sidebar').addEventListener('click', (event) => {
  const head = event.target.closest('.eyebrow');
  if (!head) return;
  const name = head.closest('section').dataset.section;
  if (folded.has(name)) folded.delete(name);
  else folded.add(name);
  remember(FOLDED_KEY, [...folded]);
  applyFolds();
});
applyFolds();
// Every row and every close mark wears the drawn set, never a typed glyph.
for (const row of document.querySelectorAll('.sidebar [data-icon]')) row.prepend(icon(row.dataset.icon));
for (const mark of document.querySelectorAll('[data-icon-only]')) mark.replaceChildren(icon(mark.dataset.iconOnly));

setPanels(loadPanels(), { keep: false });
subscribe(render);
product.home().then((where) => {
  update({ home: where || null });
  return where
    ? Promise.all([loadView(), loadFolders(), albumsPanel.refresh(), peoplePanel.refresh(), labelsPanel.refresh()])
    : chooseHome();
});
followLibrary();
