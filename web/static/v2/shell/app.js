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
import { createTimeline } from './timeline.js';

const PAGE = 200;
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
    note.textContent = 'This photograph cannot be shown.';
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
  if (stripStart === null || current < stripStart + 7 || current >= stripStart + 22) {
    stripStart = Math.max(0, Math.min(current - 14, state.total - 29));
  }
  const start = stripStart;
  const end = Math.min(state.total, start + 29);
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
    loupeStrip.replaceChildren(...cells);
  }
  const held = loupeStrip.querySelector('.is-current');
  const moved = Number(held?.dataset.index) !== current;
  if (moved) held?.classList.remove('is-current');
  const cell = loupeStrip.querySelector(`[data-index="${current}"]`);
  if (cell && (rebuilt || moved)) {
    // Centring only when the strip was rebuilt or the cursor moved: every
    // other render leaves a hand-scrolled strip where the hand put it.
    cell.classList.add('is-current');
    loupeStrip.scrollTo({
      left: cell.offsetLeft - (loupeStrip.clientWidth - cell.offsetWidth) / 2,
      behavior: rebuilt ? 'auto' : 'smooth',
    });
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

function notify(message) {
  // Every transient message rides the one toast, which floats over any
  // chrome — the sidebar status line stays the ambient truth, so folding
  // the panels never hides what the app just said.
  if (message) undo.show(message);
  else undo.hide();
}
const driveList = document.querySelector('[data-drive-list]');
let drivesSeen = '';

const DENSITY_KEY = 'azimuth.row-height';
const SORT_KEY = 'azimuth.sort';
let rowHeight = recall(DENSITY_KEY, 220);
let scrollFrame = null;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// What the window is looking at, as the bridge speaks it. Null when it is
// the whole library, so the server sees "no view" rather than three empties.
function viewOf() {
  const { folders, album, chips, expanded } = read();
  const opened = [...(expanded || [])];
  if (!(folders || []).length && !album && !(chips || []).length && !opened.length) return null;
  return opened.length ? { folders, album, chips, expanded: opened } : { folders, album, chips };
}

// Open or close one stack in place: its members take their seats after the
// cover and the rest of the grid stays where it is.
async function toggleStack(coverId) {
  const expanded = new Set(read().expanded);
  if (expanded.has(coverId)) expanded.delete(coverId);
  else expanded.add(coverId);
  update({ expanded });
  try {
    await refreshInPlace({ shelves: false });
    const { view, sort } = read();
    if (view === 'library' && (sort === 'newest' || sort === 'oldest') && !seeking()) {
      update({ days: await product.days(viewOf()) });
    }
  } catch (error) {
    notify(error.message);
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
  if (query && !(chips || []).some((c) => c.is === 'label')) return query;
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
    notify(yes
      ? (ids.length === 1 ? `Anchored to “${word}”.` : `${ids.length} anchored to “${word}”.`)
      : (ids.length === 1 ? `Not “${word}” — learning.` : `${ids.length} excluded from “${word}” — learning.`));
    void labelsPanel.refresh();
    await refreshInPlace();
  } catch (error) {
    notify(error.message);
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
    notify(`Some photos could not be loaded. ${error.message}`);
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
  product,
  notify,
  // Import is its own workspace: entering and leaving it is a view change
  // like any other, and the chrome follows.
  enter: () => { if (read().view !== 'import') update({ view: 'import' }); },
  leave: () => { if (read().view === 'import') update({ view: 'library' }); },
  isShown: () => read().view === 'import',
  progressed: (text) => update({ importing: text || '' }),
  afterImport: async () => {
    // What just came in is what the person wants to see: Recently added.
    update({ view: 'library', folders: [], album: null, sort: 'added' });
    document.querySelector('[data-sort]').value = 'added';
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
  browse: (term) => browseChip({ is: 'label', values: [term] }),
});
const editPanel = createEditPanel({
  product,
  notify,
  undo,
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
  browse: (term) => browseChip({ is: 'person', values: [term] }),
  renamed: () => Promise.all([albumsPanel.refresh(), peoplePanel.refresh()]),
  ask: (title, anchor, initial) => albumsPanel.ask(title, anchor, initial),
});
// Where the loupe returns to on Esc when it was opened from somewhere
// other than the grid: a look at one card mid-round goes back to the round.
let loupeReturnsTo = null;
const rankWorkflow = createRankWorkflow({
  product,
  read,
  update,
  notify,
  undo,
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
    // sitting; the grid comes back re-read either way.
    await loadView();
  },
});
document.querySelector('[data-rank-mode]').addEventListener('change', (event) => {
  void rankWorkflow.remode(event.target.value);
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
// More like this: the same search door, asked with photographs. It always
// asks the whole library — you narrow afterwards with chips if you want —
// and the seeds themselves stay out of the answer.
function moreLikeThis(ids) {
  if (!ids.length) return;
  if (loupeOpen()) closeLoupe();
  searchBox.value = '';
  update({ view: 'library', folders: [], album: null, chips: [], query: '', like: ids,
           selected: null, selectedIndex: null });
  workspace.scrollTo({ top: 0 });
  loadView();
}
document.querySelector('[data-like-pill]').addEventListener('click', () => {
  update({ like: [] });
  loadView();
});
// The box belongs to the cards: they offer the library's shape on focus,
// narrow it as you type, and leave Enter meaning what it always meant.
createSearchCards({
  product,
  read,
  update,
  box: searchBox,
  search: runSearch,
  applyChip: (chip) => {
    // A card replaces typed words, but narrows a like-search: chips ride
    // into the search's view scope, so "similar, and portrait" composes.
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
  },
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
      : scanning ? 'Reading your photos…'
        : inAlbum ? 'Nothing in this album yet.'
          : searching ? 'Nothing matches.'
            : inFolder ? (read().folders.length === 1 ? 'Nothing in this folder yet.' : 'Nothing in these folders yet.')
              : narrowed ? 'Nothing matches these filters.' : 'No photos here yet.',
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
                : 'Add a folder to start your library.',
    emptyAction: bare ? { label: 'Add a folder', run: openDriveDialog } : null,
    select: selectPhoto,
    open: openPhoto,
    stack: (photo) => { void toggleStack(photo.id); },
    expanded: read().expanded,
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
      event.dataTransfer.setData('text/azimuth-ids', JSON.stringify(ids));
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
    timeline.follow();
  });
}

// The first run: a home is chosen once and remembered. The dialog cannot be
// dismissed, because nothing works without one; Change… opens the native
// chooser, and the proposal is the fixed local disk with the most room.
async function chooseHome() {
  homePath.textContent = await product.proposeHome();
  homeDialog.showModal();
  homeForm.querySelector('[type="submit"]').focus();
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

function openDriveDialog() {
  if (driveDialog.open) return;
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
  loupeStrip.replaceChildren();
  stripKey = '';
  stripStart = null;
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
    if (view === 'library' && !drives.length && !counts.photos) openDriveDialog();
  } catch (error) {
    if (!pages.isCurrent(requestGeneration)) return;
    update({ loading: false });
    notify(error.message);
  }
}

async function refreshInPlace({ shelves = true } = {}) {
  // The library changed under the window -- a sweep admitted photographs or
  // the worker finished one -- so re-read what is on screen without
  // resetting it. A worker tick changes only the tiles, so it re-reads only
  // the pages; the counts, drives, Trash and shelves (albums, people,
  // labels) are asked again when a sweep or a lane rewrite says they moved.
  const generation = pages.generation;
  const { view, query } = read();
  const key = JSON.stringify(viewOf());
  const held = read();
  const [counts, drives, trashCount, size, albums, people, labels] = await Promise.all([
    shelves ? product.counts() : held.counts,
    shelves ? product.drives() : held.drives,
    shelves ? product.trashCount() : held.counts.trash,
    view === 'trash' || seeking() ? Promise.resolve(0) : shelves ? product.size(viewOf()) : held.total,
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
const WORKING = {
  identity: 'Identifying photographs',
  metadata: 'Reading photographs',
  grid: 'Making tiles',
  loupe: 'Making tiles',
  embedding: 'Mapping the space',
  faces: 'Finding faces',
  photostats: 'Measuring light',
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
    if (last === null || pulse.cards !== last.cards) showCards();
    // The status line: the kind the worker is on, what it counted as left,
    // and the pace, smoothed so two seconds of luck do not make it jump.
    const pace = last === null ? 0 : Math.round((rate * 2 + (pulse.done - last.done) * 30) / 3);
    rate = pulse.doing ? pace : 0;
    const left = Object.values(pulse.left || {}).reduce((sum, n) => sum + n, 0);
    const working = pulse.doing ? { word: WORKING[pulse.doing] || 'Working', left, rate } : null;
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
        notify(error.message);
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
      notify('Forgotten. It comes back, with its decisions, if the file ever does.');
    }
  } catch (error) {
    notify(error.message);
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
  notify(`Exporting ${ids.length.toLocaleString()} photograph${ids.length === 1 ? '' : 's'}…`);
  product.exportPhotos(ids, quality, edge, rename).then((said) => {
    if (!said.chosen) { notify(''); return; }
    const parts = [];
    if (said.exported) parts.push(`${said.exported.toLocaleString()} exported`);
    if (said.missing) parts.push(`${said.missing.toLocaleString()} not here`);
    if (said.failed) parts.push(`${said.failed.toLocaleString()} failed`);
    notify(`${parts.join(', ') || 'Nothing exported'} — ${said.destination}`);
  }).catch((error) => notify(error.message));
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
    notify(`${result.forgotten.toLocaleString()} missing photograph${result.forgotten === 1 ? '' : 's'} forgotten. They come back, with their decisions, if the files ever do.`);
  } catch (error) {
    folderMenu.hidden = true;
    disarmForget();
    notify(error.message);
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
    notify(`Synchronized: ${added} added, ${moved} moved, ${retired} no longer there.`);
  } catch (error) {
    notify(error.message);
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
    driveMenu.style.left = `${event.clientX}px`;
    driveMenu.style.top = `${event.clientY}px`;
    driveMenu.hidden = false;
    return;
  }
  const row = event.target.closest('.folder-row');
  const all = event.target.closest('.nav-row[data-action="all-photos"]');
  if (!row && !all) {
    folderMenu.hidden = true;
    driveMenu.hidden = true;
    return;
  }
  event.preventDefault();
  folderMenu.dataset.folder = row ? row.dataset.folder : '';
  disarmForget();
  folderMenu.style.left = `${event.clientX}px`;
  folderMenu.style.top = `${event.clientY}px`;
  folderMenu.hidden = false;
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('[data-folder-menu]')) folderMenu.hidden = true;
  if (!event.target.closest('[data-drive-menu]')) driveMenu.hidden = true;
});

async function loadFolders() {
  // The tree is every tail's folders with counts and a safety word; it costs
  // about a second on a large library and changes only when a sweep or an
  // import changes tails, so it is read at boot and after a sweep, never on
  // the first-paint path.
  try {
    update({ tree: await product.folders() });
  } catch (error) {
    notify(error.message);
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
      await delay(500);
      await refreshInPlace();
    }
    const result = await scan;
    await refreshInPlace();
    await loadFolders();
    notify(result.applied
      ? `${result.photos_added.toLocaleString()} photos added.`
      : result.reason || 'The folder could not be fully read.');
  } catch (error) {
    notify(error.message);
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
      if (read().selected?.id === photo.id) notify(error.message);
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
  // The cursor moves now; the row it lands on may still be on its way. A
  // run of arrows then counts from where the cursor is, not where the last
  // row arrived, so no press is lost at a page boundary.
  update({ selectedIndex: bounded });
  if (!read().photos.has(bounded)) await pages.ensure(bounded);
  if (read().selectedIndex !== bounded) return;
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

function renderLoupe(photo) {
  const source = photo.loupe || photo.tile || '';
  const note = document.querySelector('[data-loupe-note]');
  note.textContent = source ? ''
    : photo.tile_failed || photo.loupe_failed ? 'This photograph cannot be shown.'
      : photo.reachable ? 'Preparing this photograph…'
        : 'The drive that holds this photograph is away.';
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

function openPhoto(index) {
  if (read().selectedIndex !== index) selectPhoto(index);
  const photo = read().photos.get(index);
  if (photo) showPhoto(photo);
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
      const row = document.createElement('div');
      row.className = 'folder-row' + (state.view === 'library' && (state.folders || []).includes(node.path) ? ' is-active' : '');
      row.style.setProperty('--depth', depth);
      row.dataset.folder = node.path;
      row.title = node.path;
      const open = state.open.has(node.path);
      const disclosure = document.createElement('button');
      disclosure.type = 'button';
      disclosure.className = 'disclosure' + (node.children.length ? ' has-children' : '') + (open ? ' is-open' : '');
      disclosure.dataset.toggle = node.path;
      disclosure.setAttribute('aria-label', open ? 'Collapse' : 'Expand');
      disclosure.textContent = '▶';
      const safety = document.createElement('span');
      safety.className = `safety ${node.safety === 'unknown' && !anyRecord ? 'quiet' : node.safety}`;
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
  document.querySelector('[data-folder-tree]').replaceChildren(...rows);
}

function renderChrome(state) {
  albumsPanel.render(state);
  peoplePanel.render(state);
  labelsPanel.render(state);
  filterBar.render(state);
  timeline.render(state);
  library.renderInspector(inspector.querySelector('[data-inspector-facts]'), state.selected);
  editPanel.follows(state.view === 'loupe' ? state.selected : null);
  if (state.view === 'loupe' && state.selected) {
    renderLoupe(state.selected);
    renderStrip(state);
    if (state.selectedIndex !== null) warmNeighbours(state.selectedIndex);
  }
  const count = state.counts.photos.toLocaleString();
  // The import workspace's own panel carries its counts; the library's
  // number beside the word Import would be someone else's answer.
  document.querySelector('[data-photo-count]').textContent = state.view === 'import'
    ? ''
    : state.view === 'people'
      ? `${(state.people || []).length.toLocaleString()} people`
      : `${state.total.toLocaleString()} photos`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-trash-count]').textContent = state.counts.trash.toLocaleString();
  // The label speaks about the person's photographs, not the app's memory:
  // how many are selected, or nothing — the title already carries the count.
  document.querySelector('[data-result-label]').textContent = state.loading
    ? 'Loading your library…'
    : state.marked?.size > 1 ? `${state.marked.size.toLocaleString()} selected` : '';
  // The running import outranks the reading chatter: it is the one thing
  // the person just asked for.
  // Always a sentence, never a blank: the import you asked for, the sweep,
  // the worker's current kind with what is left and how fast, or the calm.
  const working = state.working;
  status.textContent = state.importing
    ? state.importing
    : state.scanning
      ? 'Reading your photos…'
      : working
        ? [working.word,
           working.left ? `${working.left.toLocaleString()} left` : null,
           working.rate ? `${working.rate.toLocaleString()} / min` : null,
          ].filter(Boolean).join(' · ')
        : state.counts.unidentified
          ? `Reading ${state.counts.unidentified.toLocaleString()} photos…`
          : `Up to date · ${count} photos`;
  const searching = state.view === 'library' && Boolean(state.query || (state.like || []).length);
  const ranking = state.view === 'rank';
  const holding = state.view === 'loupe';
  const walled = state.view === 'people';
  const intaking = state.view === 'import';
  shell.classList.toggle('hide-left', !state.panels.left);
  shell.classList.toggle('hide-right', !state.panels.right);
  shell.classList.toggle('hide-top', !state.panels.top);
  loupe.hidden = !holding;
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
  pill.hidden = !alike || state.view === 'trash' || holding || walled;
  if (alike) pill.textContent = `≈ More like ${alike === 1 ? 'this photo' : `${alike} photos`} ✕`;
  document.querySelector('[data-sort]').closest('label').hidden = state.view === 'trash' || ranking || searching || holding || walled || intaking;
  // A decision is keyed on identity, and identity arrives shortly after a
  // sweep; until then the photograph cannot take one, so nothing offers to.
  const canCull = (state.view === 'library' || state.view === 'loupe') && selection().length > 0;
  document.querySelector('[data-cull-actions]').hidden = !canCull;
  const many = (state.marked?.size || 0) > 1;
  document.querySelector('[data-action="pick"]').hidden = !canCull || (!many && state.selected?.status === 'picked');
  document.querySelector('[data-action="clear-pick"]').hidden = !canCull || (!many && state.selected?.status !== 'picked');
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

  const drivesKey = state.drives.map((drive) => `${drive.uuid}.${drive.attached ? 1 : 0}.${drive.label || drive.root}`).join('|');
  if (drivesKey !== drivesSeen) {
    drivesSeen = drivesKey;
    driveList.replaceChildren(...state.drives.map((drive) => {
      const row = document.createElement('div');
      row.className = 'drive-row';
      row.dataset.drive = drive.uuid;
      row.title = `${drive.root}${drive.attached ? '' : ' — away'}`;
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
    const root = await product.chooseFolder();
    if (!root) return;
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
  if (action === 'add-drive') openDriveDialog();
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
    if (read().view === 'rank') update({ folders: [], album: null, chips: [], query: '' });
    else if (read().view === 'trash' && parked?.key === JSON.stringify({ ...viewOf() })) {
      update({ view: 'library', selected: null, selectedIndex: null });
      void unpark();
      return;
    } else update({ view: 'library', folders: [], album: null, chips: [], query: '',
                    selected: null, selectedIndex: null });
    viewMoved();
  }
  if (action === 'trash-view' && read().view !== 'trash') {
    // A peek at Trash remembers where the library was; leaving lands back
    // there, cursor and scroll intact, if the view is the same one.
    parked = { key: JSON.stringify(viewOf()), scrollTop: workspace.scrollTop,
               index: read().selectedIndex, id: read().selected?.id ?? null };
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
    notify('Writing sidecars…');
    product.exportFolder(folder).then((said) => {
      const parts = [];
      if (said.written) parts.push(`${said.written.toLocaleString()} written`);
      if (said.unchanged) parts.push(`${said.unchanged.toLocaleString()} already current`);
      if (said.missing) parts.push(`${said.missing.toLocaleString()} not here`);
      notify(`Lightroom metadata: ${parts.join(', ') || 'nothing to write'}.`);
    }).catch((error) => notify(error.message));
  }
  if (action === 'adopt-track') {
    folderMenu.hidden = true;
    product.adoptTrack().then((said) => {
      if (said.chosen) notify(said.placed ? `${said.placed.toLocaleString()} photographs placed from the track.` : 'The track covers none of your photographs — check the camera clock.');
    }).catch((error) => notify(error.message));
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
    else product.chooseFolder().then((chosen) => { if (chosen) intakeWorkflow.open(chosen); }).catch((error) => notify(error.message));
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
  if (action === 'pick') cullWorkflow.apply('pick');
  if (action === 'clear-pick') cullWorkflow.apply('clear');
  if (action === 'turn') cullWorkflow.apply(event.shiftKey ? 'turnRight' : 'turnLeft');
  if (action === 'reject') cullWorkflow.apply('reject');
  if (action === 'empty-trash') trashWorkflow.openDialog();
  if (action === 'undo-toast') undo.run();
  if (action === 'close-drive') closeDriveDialog();
  if (action === 'close-empty') trashWorkflow.closeDialog();
  if (action === 'close-loupe') closeLoupe();
});

document.addEventListener('keydown', (event) => {
  const target = event.target;
  const isTyping = target.matches('input, select, textarea, [contenteditable="true"]');
  if (homeDialog.open || exportDialog.open) return;
  if (intakeWorkflow.isOpen()) {
    if (event.key === 'Escape') intakeWorkflow.close();
    if (event.key === 'Enter' && !isTyping) {
      intakeWorkflow.finish();
      event.preventDefault();
    }
    if (event.key === ' ' && !isTyping) {
      intakeWorkflow.toggleSelected();
      event.preventDefault();
    }
    return;
  }
  if (cropSurface.isOpen()) {
    if (cropSurface.key(event)) event.preventDefault();
    return;
  }
  if (editPanel.isOpen() && event.key === 'Escape' && !isTyping) {
    editPanel.close();
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
    }).catch((error) => notify(error.message));
    return;
  }
  if (event.key === '/' && !isTyping) {
    searchBox.focus();
    searchBox.select();
    event.preventDefault();
    return;
  }
  if (event.key.toLowerCase() === 's' && !isTyping && !event.ctrlKey && !event.metaKey
      && read().view === 'library' && read().selected) {
    // S opens or closes the set the selected photograph belongs to, cover or
    // member alike — Lightroom's key, the grid's own grammar.
    const held = read().selected;
    const cover = held.stack_of || (held.stack ? held.id : null);
    if (cover) {
      void toggleStack(cover);
      event.preventDefault();
    }
    return;
  }
  if (event.key === 'Escape') {
    // One rung at a time: a menu, then the zoom, then the clean room, then
    // the loupe itself — the popovers close in their own earlier handlers.
    if (!folderMenu.hidden || !driveMenu.hidden) {
      folderMenu.hidden = true;
      driveMenu.hidden = true;
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
    else if (trashWorkflow.isOpen()) trashWorkflow.closeDialog();
    else if (driveDialog.open) closeDriveDialog();
    else if (read().selected || read().marked?.size) {
      // The marks and the selection go; the cursor keeps its place, so the
      // next arrow moves from here and not from the top.
      update({ selected: null, marked: new Set() });
      anchorIndex = null;
    } else if (seeking()) { searchBox.value = ''; runSearch(''); }
    else if (read().chips.length) { update({ chips: [] }); viewMoved(); }
    else return;
    event.preventDefault();
    return;
  }
  if (isTyping || driveDialog.open || trashWorkflow.isOpen()) return;
  if (event.key === 'Tab') {
    // On an empty library the only thing worth reaching is the one button in
    // the empty state; folding panels there would strand the keyboard.
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
    if (rankWorkflow.key(event)) event.preventDefault();
    return;
  }

  const current = read().selectedIndex;
  const key = event.key.toLowerCase();
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
      void editPanel.open(read().selected);
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
  const cullActions = { p: 'pick', u: 'clear', x: 'reject', r: event.shiftKey ? 'turnRight' : 'turnLeft' };
  if (key in cullActions && ['library', 'loupe'].includes(read().view) && selection().length) {
    cullWorkflow.apply(cullActions[key]);
    event.preventDefault();
    return;
  }
  if (read().view === 'people') return;   // the wall has no grid cursor
  const extend = { shift: event.shiftKey };
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
workspace.addEventListener('wheel', (event) => {
  // Ctrl+wheel is the grid's own zoom: the same slider, driven from where
  // the eyes already are.
  if (!event.ctrlKey || read().view !== 'library') return;
  event.preventDefault();
  const next = Math.max(Number(density.min), Math.min(Number(density.max),
    rowHeight - Math.sign(event.deltaY) * Number(density.step || 10)));
  if (next === rowHeight) return;
  density.value = next;
  density.dispatchEvent(new Event('input'));
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
        notify(`${said.written} sidecar${said.written === 1 ? '' : 's'} written`
          + (said.unchanged ? `, ${said.unchanged} already current` : '')
          + (said.missing ? `, ${said.missing} not here` : '') + '.');
      } catch (error) {
        notify(error.message);
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
const FOLDED_KEY = 'azimuth.folded-sections';
const folded = new Set(recall(FOLDED_KEY, []));
function applyFolds() {
  for (const section of document.querySelectorAll('.sidebar section')) {
    const name = section.querySelector('.eyebrow')?.textContent || '';
    section.classList.toggle('is-folded', folded.has(name));
  }
}
document.querySelector('.sidebar').addEventListener('click', (event) => {
  const head = event.target.closest('.eyebrow');
  if (!head) return;
  const name = head.textContent;
  if (folded.has(name)) folded.delete(name);
  else folded.add(name);
  remember(FOLDED_KEY, [...folded]);
  applyFolds();
});
applyFolds();

setPanels(loadPanels(), { keep: false });
subscribe(render);
product.home().then((where) => (where
  ? Promise.all([loadView(), loadFolders(), albumsPanel.refresh(), peoplePanel.refresh(), labelsPanel.refresh()])
  : chooseHome()));
followLibrary();
