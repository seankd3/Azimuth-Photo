import { library as product } from '../net/index.js';
import { PageCache } from '../kit/page-cache.js';
import { getLens, read, subscribe, update } from '../store/index.js';
import { createCullWorkflow, CULL_MENU } from './cull.js';
import { createCollectionsPanel } from './collections.js';
import { createFilterBar } from './filters.js';
import { createIntakeWorkflow } from './intake.js';
import { createLoupe } from './loupe.js';
import { createRefineWorkflow } from './refine.js';
import { createTrashWorkflow } from './trash.js';
import { createUndo } from './undo.js';

const PAGE = 200;
const CONTEXTBAR_HEIGHT = 46;
const library = getLens('library');
const workspace = document.querySelector('.workspace');
const grid = document.querySelector('[data-grid]');
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
  try {
    const held = JSON.parse(localStorage.getItem(PANELS_KEY) || '');
    return { left: held.left !== false, right: held.right !== false, top: held.top !== false };
  } catch {
    return { left: true, right: true, top: true };
  }
}
function setPanels(panels, { remember = true } = {}) {
  update({ panels });
  if (remember) localStorage.setItem(PANELS_KEY, JSON.stringify(panels));
}
function togglePanel(side) {
  const panels = { ...read().panels, [side]: !read().panels[side] };
  setPanels(panels);
}
let panelsBeforeFull = null;

// The photographs beside this one, already decoded, so an arrow is one
// frame — the same warming Refine's stage uses. A small held set keeps the
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
    signature += photo ? `|${photo.id}.${photo.tile ? 1 : 0}.${photo.status}.${photo.rotate}` : '|·';
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
  if (Number(held?.dataset.index) !== current) held?.classList.remove('is-current');
  const cell = loupeStrip.querySelector(`[data-index="${current}"]`);
  if (cell) {
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
    setPanels({ left: false, right: false, top: false }, { remember: false });
  } else {
    setPanels(panelsBeforeFull || loadPanels(), { remember: false });
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

let rowHeight = 220;
let scrollFrame = null;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// What the window is looking at, as the bridge speaks it. Null when it is
// the whole library, so the server sees "no view" rather than three empties.
function viewOf() {
  const { folder, collection, chips } = read();
  if (!folder && !collection && !(chips || []).length) return null;
  return { folder, collection, chips };
}

// What a verb acts on: the marked set when there is one, else the focused
// photograph. One answer for cull, Quick, menus and drags.
function selection() {
  const { marked, selected } = read();
  if (marked && marked.size) return [...marked];
  return selected ? [selected.id] : [];
}
let anchorIndex = null;
const pages = new PageCache({
  pageSize: PAGE,
  load: (offset, limit) => read().view === 'trash'
    ? product.trashPhotos({ limit, offset })
    : read().query
      ? product.find({ query: read().query, limit, offset, view: viewOf() }).then((answer) => answer.photos)
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
  afterImport: async () => {
    // What just came in is what the person wants to see: Recently added.
    update({ view: 'library', folder: null, collection: null, sort: 'added' });
    document.querySelector('[data-sort]').value = 'added';
    await Promise.all([loadView(), loadFolders(), collectionsPanel.refresh()]);
  },
});
const collectionsPanel = createCollectionsPanel({
  product,
  read,
  update,
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
const refineWorkflow = createRefineWorkflow({
  product,
  read,
  update,
  notify,
  undo,
  viewOf,
  onLeave: async () => {
    // Rounds moved the ranking, and the view may have moved under the
    // sitting; the grid comes back re-read either way.
    await loadView();
  },
});
let collectionsTimer = null;
function refreshCollectionsSoon() {
  // Smart-collection counts follow a cull within a beat, not on the next import.
  clearTimeout(collectionsTimer);
  collectionsTimer = setTimeout(() => collectionsPanel.refresh(), 1200);
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
    refreshCollectionsSoon();
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
    refreshCollectionsSoon();
    await selectIndex(Math.min(selectedIndex ?? 0, Math.max(0, read().total - 1)));
  },
  selectIndex,
  notify,
  undo,
});

const searchBox = document.querySelector('[data-search]');
let searchTimer = null;
function runSearch(text) {
  clearTimeout(searchTimer);
  const query = text.trim();
  if (query === read().query) return;
  update({ view: 'library', query });
  workspace.scrollTo({ top: 0 });
  loadView();
}
searchBox.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => runSearch(searchBox.value), 300);
});
searchBox.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    runSearch(searchBox.value);
    event.preventDefault();
  }
  if (event.key === 'Escape') {
    searchBox.value = '';
    runSearch('');
    searchBox.blur();
    event.stopPropagation();
    event.preventDefault();
  }
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
  const searching = Boolean(read().query);
  const scanning = read().scanning;
  library.renderGrid(grid, read(), {
    // While a sweep is reading a folder the library is not empty, it is
    // arriving — the one moment "Add a folder" must not be the message.
    emptyTitle: inTrash ? 'Trash is empty.'
      : scanning ? 'Reading your photos…'
        : searching ? 'Nothing matches.' : 'No photos here yet.',
    emptyCopy: inTrash
      ? 'Rejected photographs stay recoverable here until you empty Trash.'
      : scanning
        ? 'They appear here as they are found.'
        : searching
          ? 'Try fewer words, or a different idea — meaning works too, not just names.'
          : 'Add a folder to start your library.',
    emptyAction: inTrash || searching || scanning ? null : { label: 'Add a folder', run: openDriveDialog },
    select: selectPhoto,
    open: openPhoto,
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
  if (loupeOpen()) update({ view: 'library' });
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
    const { sort, view, folder, query } = read();
    const looking = viewOf();
    const key = JSON.stringify(looking);
    const [counts, drives, trashCount, size, page] = await Promise.all([
      product.counts(),
      product.drives(),
      product.trashCount(),
      view === 'trash' || query ? Promise.resolve(0) : product.size(looking),
      view === 'trash'
        ? product.trashPhotos({ limit: PAGE, offset: 0 })
        : query
          ? product.find({ query, limit: PAGE, offset: 0, view: looking })
          : product.photos({ sort, limit: PAGE, offset: 0, view: looking }),
    ]);
    if (!pages.isCurrent(requestGeneration) || read().view !== view
        || JSON.stringify(viewOf()) !== key || read().query !== query) return;
    const total = view === 'trash' ? trashCount : query ? page.total : size;
    pages.seed(requestGeneration, query ? page.photos : page, total);
    update({ counts: { ...counts, trash: trashCount }, drives, loading: false });
    if (view === 'library' && !drives.length && !counts.photos) openDriveDialog();
  } catch (error) {
    if (!pages.isCurrent(requestGeneration)) return;
    update({ loading: false });
    notify(error.message);
  }
}

async function refreshInPlace() {
  // The library changed under the window -- a sweep admitted photographs or
  // the worker finished one -- so re-read what is loaded without resetting it.
  const generation = pages.generation;
  const { view, query } = read();
  const key = JSON.stringify(viewOf());
  const [counts, drives, trashCount, size, collections] = await Promise.all([
    product.counts(), product.drives(), product.trashCount(),
    view === 'trash' || query ? Promise.resolve(0) : product.size(viewOf()),
    product.collections(),
  ]);
  if (!pages.isCurrent(generation) || read().view !== view
      || JSON.stringify(viewOf()) !== key || read().query !== query) return;
  await pages.refresh(view === 'trash' ? trashCount : query ? read().total : size);
  if (!pages.isCurrent(generation)) return;
  update({ counts: { ...counts, trash: trashCount }, drives, collections });
  const { selected, photos } = read();
  if (!selected) return;
  for (const [index, photo] of photos) {
    if (photo.id === selected.id) {
      update({ selected: { ...selected, ...photo }, selectedIndex: index });
      return;
    }
  }
}

async function followLibrary() {
  // The worker identifies, reads and renders in the background. The window
  // learns of it by asking one free question every couple of seconds and
  // re-reading what it holds only when the answer moved.
  let last = null;
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
    if (last === null || pulse.cards !== last.cards) showCards();
    last = pulse;
    if ((moved || swept) && !read().loading && !read().scanning) {
      try {
        await refreshInPlace();
        if (swept) await loadFolders();
      } catch (error) {
        notify(error.message);
      }
    }
  }
}

const folderMenu = document.querySelector('[data-folder-menu]');
const confirmDialog = document.querySelector('[data-confirm-dialog]');

// One plain question for the acts that deserve a beat: a title, what it
// means, and the verb itself on the button. Emptying Trash keeps its typed
// count — that one is irreversible; these are merely large.
function askConfirm({ title, copy, verb }) {
  return new Promise((resolve) => {
    confirmDialog.querySelector('[data-confirm-title]').textContent = title;
    confirmDialog.querySelector('[data-confirm-copy]').textContent = copy;
    confirmDialog.querySelector('[data-confirm-yes]').textContent = verb;
    confirmDialog.returnValue = '';
    confirmDialog.addEventListener('close', () => resolve(confirmDialog.returnValue === 'yes'), { once: true });
    confirmDialog.showModal();
    confirmDialog.querySelector('[data-confirm-yes]').focus();
  });
}

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

async function forgetMissing(folder) {
  folderMenu.hidden = true;
  try {
    // Count first, then ask with the number in the question.
    const probe = await product.forgetMissing(folder || '', true);
    if (!probe.forgotten) {
      notify('Nothing is missing here.');
      return;
    }
    const n = probe.forgotten;
    const where = folder ? `under ${folder.split('/').pop()}` : 'everywhere in the library';
    const sure = await askConfirm({
      title: `Forget ${n.toLocaleString()} missing photograph${n === 1 ? '' : 's'}?`,
      copy: `Rows ${where} whose files no attached drive holds leave the catalog. They come back, with their decisions, if the files ever do.`,
      verb: n === 1 ? 'Forget it' : `Forget ${n.toLocaleString()}`,
    });
    if (!sure) return;
    const result = await product.forgetMissing(folder || '');
    await Promise.all([loadView(), loadFolders()]);
    notify(`${result.forgotten.toLocaleString()} missing photograph${result.forgotten === 1 ? '' : 's'} forgotten.`);
  } catch (error) {
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
    update({ folders: await product.folders() });
  } catch (error) {
    notify(error.message);
  }
}

// The view moved under whatever stage is up: Refine re-scopes in place, the
// grid starts from the top of the new answer. Folders, collections, All
// photos and the chips all route through here — one rule, one place.
function viewMoved() {
  if (refineWorkflow.isOpen()) {
    refineWorkflow.resize(refineWorkflow.size());
    return;
  }
  workspace.scrollTo({ top: 0 });
  loadView();
}

function showFolder(path) {
  if (read().query) {
    searchBox.value = '';
    update({ query: '' });
  }
  if (read().view === 'refine') update({ folder: path, collection: null });
  else update({ view: 'library', folder: path, collection: null, selected: null, selectedIndex: null });
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

async function selectPhoto(index, modifiers = {}) {
  // The row as it is now; a cell's click never carries a row of its own.
  const photo = read().photos.get(index);
  if (!photo) return;
  const marked = new Set(read().marked);
  if (modifiers.shift && anchorIndex !== null) {
    // The range covers what is loaded between the anchor and here; sparse
    // pages contribute what they hold.
    for (let i = Math.min(anchorIndex, index); i <= Math.max(anchorIndex, index); i += 1) {
      const held = read().photos.get(i);
      if (held) marked.add(held.id);
    }
  } else if (modifiers.toggle) {
    if (marked.has(photo.id)) marked.delete(photo.id);
    else marked.add(photo.id);
    anchorIndex = index;
  } else {
    marked.clear();
    marked.add(photo.id);
    anchorIndex = index;
  }
  update({ selected: photo, selectedIndex: index, marked });
  try {
    const details = await product.photo(photo.id);
    if (read().selected?.id === photo.id) update({ selected: { ...read().selected, ...details } });
  } catch (error) {
    if (read().selected?.id === photo.id) notify(error.message);
  }
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
  if (!read().photos.has(bounded)) await pages.ensure(bounded);
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
  const key = `${state.view}|${state.folder}|${[...state.open].join(',')}|${anyRecord}`;
  if (foldersSeen === state.folders && foldersKey === key) return;
  foldersSeen = state.folders;
  foldersKey = key;
  const rows = [];
  const walk = (nodes, depth) => {
    for (const node of nodes) {
      const row = document.createElement('div');
      row.className = 'folder-row' + (state.view === 'library' && state.folder === node.path ? ' is-active' : '');
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
  walk(state.folders, 0);
  document.querySelector('[data-folder-tree]').replaceChildren(...rows);
}

function renderChrome(state) {
  collectionsPanel.render(state);
  filterBar.render(state);
  library.renderInspector(inspector, state.selected);
  if (state.view === 'loupe' && state.selected) {
    renderLoupe(state.selected);
    renderStrip(state);
    if (state.selectedIndex !== null) warmNeighbours(state.selectedIndex);
  }
  const count = state.counts.photos.toLocaleString();
  document.querySelector('[data-photo-count]').textContent = `${state.total.toLocaleString()} photos`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-trash-count]').textContent = state.counts.trash.toLocaleString();
  // The label speaks about the person's photographs, not the app's memory:
  // how many are selected, or nothing — the title already carries the count.
  document.querySelector('[data-result-label]').textContent = state.loading
    ? 'Loading your library…'
    : state.marked?.size > 1 ? `${state.marked.size.toLocaleString()} selected` : '';
  status.textContent = state.scanning
    ? 'Reading your photos…'
    : state.counts.unidentified
      ? `Reading ${state.counts.unidentified.toLocaleString()} photos…`
      : '';
  const searching = state.view === 'library' && Boolean(state.query);
  const refining = state.view === 'refine';
  const holding = state.view === 'loupe';
  shell.classList.toggle('hide-left', !state.panels.left);
  shell.classList.toggle('hide-right', !state.panels.right);
  shell.classList.toggle('hide-top', !state.panels.top);
  loupe.hidden = !holding;
  grid.hidden = refining || holding;
  document.querySelector('[data-refine]').hidden = !refining;
  document.querySelector('[data-refine-progress]').hidden = !refining;
  document.querySelector('[data-refine-size]').hidden = !refining;
  document.querySelector('[data-action="refine"]').hidden = state.view !== 'library' || searching;
  document.querySelector('[data-action="leave-refine"]').hidden = !refining;
  document.querySelector('[data-result-label]').hidden = refining || holding;
  document.querySelector('[data-density]').closest('label').hidden = refining || holding;
  // The chips narrow the library view; Trash and the loupe are not places
  // to edit them, so they leave with their + button.
  document.querySelector('[data-chips]').hidden = state.view === 'trash' || holding;
  document.querySelector('[data-sort]').closest('label').hidden = state.view === 'trash' || refining || searching || holding;
  // A decision is keyed on identity, and identity arrives shortly after a
  // sweep; until then the photograph cannot take one, so nothing offers to.
  const canCull = (state.view === 'library' || state.view === 'loupe') && Boolean(state.selected?.hash);
  document.querySelector('[data-cull-actions]').hidden = !canCull;
  document.querySelector('[data-action="pick"]').hidden = !canCull || state.selected.status === 'picked';
  document.querySelector('[data-action="clear-pick"]').hidden = !canCull || state.selected.status !== 'picked';
  document.querySelector('[data-action="forget"]').hidden = !(state.view === 'library' && state.selected && state.selected.placed === 0);
  const restorable = state.view === 'trash' && (state.marked?.size || state.selected);
  const restore = document.querySelector('[data-action="restore"]');
  restore.hidden = !restorable;
  restore.textContent = state.marked?.size > 1 ? `Restore ${state.marked.size}` : 'Restore';
  document.querySelector('[data-action="empty-trash"]').hidden = state.view !== 'trash' || !state.counts.trash;
  document.querySelector('.nav-row[data-action="all-photos"]').classList.toggle('is-active', state.view === 'library' && !state.folder);
  document.querySelector('.nav-row[data-action="trash-view"]').classList.toggle('is-active', state.view === 'trash');
  const shelf = (state.collections || []).find((c) => c.id === state.collection);
  const where = shelf ? shelf.name : state.folder ? state.folder.split('/').pop() : '';
  document.querySelector('.view-title strong').textContent = state.view === 'trash'
    ? 'Trash'
    : searching ? `Results for “${state.query}”${where ? ` in ${where}` : ''}`
    : (refining ? 'Refine · ' : '') + (where || 'All photos');
  document.querySelector('[data-action="add-chip"]').hidden = state.view !== 'library' || refining;
  document.querySelector('[data-action="save-view"]').hidden =
    state.view !== 'library' || refining || searching || !viewOf();
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
  if (state.view !== 'refine' && state.view !== 'loupe') visibleGrid();
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
    showFolder(folderRow.dataset.folder);
    return;
  }
  if (action === 'all-photos') {
    searchBox.value = '';
    if (read().view === 'refine') update({ folder: null, collection: null, chips: [], query: '' });
    else update({ view: 'library', folder: null, collection: null, chips: [], query: '',
                  selected: null, selectedIndex: null });
    viewMoved();
  }
  if (action === 'trash-view') {
    update({ view: 'trash' });
    workspace.scrollTo({ top: 0 });
    loadView();
  }
  if (action === 'restore') trashWorkflow.restoreSelected();
  if (action === 'forget') forgetSelected();
  if (action === 'synchronize-folder') synchronizeFolder(folderMenu.dataset.folder);
  if (action === 'forget-missing') forgetMissing(folderMenu.dataset.folder);
  if (action === 'rescan-drive') {
    driveMenu.hidden = true;
    const drive = read().drives.find((d) => d.uuid === driveMenu.dataset.drive);
    if (drive) scanDrive(drive);
  }
  if (action === 'import-folder') {
    product.chooseFolder().then((chosen) => { if (chosen) intakeWorkflow.open(chosen); }).catch((error) => notify(error.message));
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
  if (action === 'new-collection') collectionsPanel.create(event.target);
  if (action === 'save-view') collectionsPanel.saveView(event.target);
  if (action === 'keep-results') collectionsPanel.keepResults(event.target);
  if (action === 'refine') refineWorkflow.open();
  if (action === 'leave-refine') refineWorkflow.close();
  const size = event.target.closest('[data-refine-size] [data-size]')?.dataset.size;
  if (size) refineWorkflow.resize(Number(size));
  if (action === 'pick') cullWorkflow.apply('pick');
  if (action === 'clear-pick') cullWorkflow.apply('clear');
  if (action === 'reject') cullWorkflow.apply('reject');
  if (action === 'empty-trash') trashWorkflow.openDialog();
  if (action === 'undo-toast') undo.run();
  if (action === 'close-drive') closeDriveDialog();
  if (action === 'confirm-no') confirmDialog.close('');
  if (action === 'close-empty') trashWorkflow.closeDialog();
  if (action === 'close-loupe') closeLoupe();
});

document.addEventListener('keydown', (event) => {
  const target = event.target;
  const isTyping = target.matches('input, select, textarea, [contenteditable="true"]');
  if (homeDialog.open || confirmDialog.open) return;
  if (intakeWorkflow.isOpen()) {
    if (event.key === 'Escape') intakeWorkflow.close();
    if (event.key === 'Enter' && !isTyping) {
      intakeWorkflow.finish();
      event.preventDefault();
    }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !isTyping) {
    undo.run();
    event.preventDefault();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a' && !isTyping
      && ['library', 'trash'].includes(read().view)) {
    // Everything the window holds; the label says how many that is.
    const marked = new Set([...read().photos.values()].map((photo) => photo.id));
    if (marked.size) update({ marked });
    event.preventDefault();
    return;
  }
  if (event.key === '/' && !isTyping) {
    searchBox.focus();
    searchBox.select();
    event.preventDefault();
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
      if (!loupeView.escape()) {
        if (loupe.classList.contains('is-full')) toggleFull(false);
        else closeLoupe();
      }
    }
    else if (refineWorkflow.isOpen()) refineWorkflow.close();
    else if (trashWorkflow.isOpen()) trashWorkflow.closeDialog();
    else if (driveDialog.open) closeDriveDialog();
    else if (read().selected || read().marked?.size) {
      update({ selected: null, selectedIndex: null, marked: new Set() });
      anchorIndex = null;
    } else if (read().query) { searchBox.value = ''; runSearch(''); }
    else if (read().chips.length) { update({ chips: [] }); loadView(); }
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
  if (refineWorkflow.isOpen()) {
    if (refineWorkflow.key(event)) event.preventDefault();
    return;
  }

  const current = read().selectedIndex;
  const key = event.key.toLowerCase();
  if (key === 'b' && ['library', 'loupe'].includes(read().view) && selection().length) {
    collectionsPanel.toss();
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
  if (key === 'u' && read().view === 'trash' && selection().length) {
    trashWorkflow.restoreSelected();
    event.preventDefault();
    return;
  }
  const cullActions = { p: 'pick', u: 'clear', x: 'reject', r: event.shiftKey ? 'turnLeft' : 'turnRight' };
  if (key in cullActions && ['library', 'loupe'].includes(read().view) && read().selected?.hash) {
    cullWorkflow.apply(cullActions[key]);
    event.preventDefault();
    return;
  }
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

document.querySelector('[data-sort]').addEventListener('change', (event) => {
  update({ sort: event.target.value });
  workspace.scrollTo({ top: 0 });
  loadView();
});

document.querySelector('[data-density]').addEventListener('input', (event) => {
  // The lens anchors the first visible cell across the re-layout itself; a
  // selection, when there is one, is what the person is looking at.
  rowHeight = Number(event.target.value);
  visibleGrid();
  if (read().selectedIndex !== null) {
    scrollIndexIntoView(read().selectedIndex);
    visibleGrid();
  }
});

grid.addEventListener('contextmenu', (event) => {
  const cell = event.target.closest('.photo-cell[data-kind="photo"]');
  if (!cell || read().view !== 'library') return;
  const index = Number(cell.dataset.index);
  const photo = read().photos.get(index);
  if (photo && !selection().includes(photo.id)) void selectPhoto(index);
  // The same verbs under the mouse as in the bar and on the keys.
  collectionsPanel.menuFor(event, CULL_MENU.map(({ action, label }) => ({
    label,
    run: () => cullWorkflow.apply(action),
  })));
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

setPanels(loadPanels(), { remember: false });
subscribe(render);
product.home().then((where) => (where
  ? Promise.all([loadView(), loadFolders(), collectionsPanel.refresh()])
  : chooseHome()));
followLibrary();
