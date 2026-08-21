import { library as product } from '../net/index.js';
import { PageCache } from '../kit/page-cache.js';
import { getLens, read, subscribe, update } from '../store/index.js';
import { createCullWorkflow } from './cull.js';
import { createCollectionsPanel } from './collections.js';
import { createFilterBar } from './filters.js';
import { createIntakeWorkflow } from './intake.js';
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
const status = document.querySelector('[data-status]');
let noticeTimer = null;

function notify(message) {
  // The status line is state: a notice, or else what the library is doing.
  // A notice is transient, so the ambient truth behind it returns by itself.
  update({ notice: message });
  clearTimeout(noticeTimer);
  if (message) {
    noticeTimer = setTimeout(() => {
      if (read().notice === message) update({ notice: '' });
    }, 8000);
  }
}
const driveList = document.querySelector('[data-drive-list]');

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
const undo = createUndo({ notify });
const trashWorkflow = createTrashWorkflow({
  product,
  read,
  reload: () => loadView(),
  notify,
  undo,
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
  selection,
  viewOf,
});
const filterBar = createFilterBar({
  product,
  read,
  update,
  onChange: () => { workspace.scrollTo({ top: 0 }); loadView(); },
});
const refineWorkflow = createRefineWorkflow({
  product,
  read,
  update,
  notify,
  undo,
  viewOf,
  onLeave: async () => {
    // The ranking moved while the stage was up; a grid sorted by it reloads.
    if (read().sort === 'best') await loadView();
  },
});
const cullWorkflow = createCullWorkflow({
  product,
  read,
  reload: () => loadView(),
  selection,
  replace: (photo) => pages.patch((item) => item.hash === photo.hash, (item) => ({ ...item, status: photo.status, rotate: photo.rotate })),
  remove: async (index, moved) => {
    // One cell left the grid: edit the window in place so the loop never
    // waits on a reload. A duplicate identity takes more than one row with
    // it, at positions this window cannot know; then the library is asked.
    if (moved !== 1) {
      await loadView();
      return;
    }
    pages.remove(index);
    const counts = read().counts;
    update({
      counts: {
        ...counts,
        photos: Math.max(0, counts.photos - moved),
        trash: counts.trash + moved,
      },
    });
    void pages.ensure(index);
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
  update({ view: 'library', query, selected: null, selectedIndex: null });
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
  library.renderGrid(grid, read(), {
    emptyTitle: inTrash ? 'Trash is empty.' : searching ? 'Nothing matches.' : 'No photos here yet.',
    emptyCopy: inTrash
      ? 'Rejected photographs stay recoverable here until you empty Trash.'
      : searching
        ? 'Try fewer words, or a different idea — meaning works too, not just names.'
        : 'Add a folder to start your library.',
    emptyAction: inTrash || searching ? null : { label: 'Add a folder', run: openDriveDialog },
    select: selectPhoto,
    open: openPhoto,
    drag: (index, event) => {
      const photo = read().photos.get(index);
      if (!photo) return;
      if (!selection().includes(photo.id)) {
        void selectPhoto(index);
        event.dataTransfer.setData('text/azimuth-ids', JSON.stringify([photo.id]));
      } else {
        event.dataTransfer.setData('text/azimuth-ids', JSON.stringify(selection()));
      }
      event.dataTransfer.effectAllowed = 'copy';
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
  if (loupe.open) loupe.close();
  loupeImage.removeAttribute('src');
  delete loupeImage.dataset.source;
  loupeImage.alt = '';
  const selectedIndex = read().selectedIndex;
  if (selectedIndex !== null) {
    requestAnimationFrame(() => grid.querySelector(`[data-index="${selectedIndex}"]`)?.focus());
  }
}

async function loadView() {
  const requestGeneration = pages.reset();
  update({ loading: true, photos: new Map(), total: 0, selected: null, selectedIndex: null });
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
    update({ notice: '' });
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
  const [counts, drives, trashCount, size] = await Promise.all([
    product.counts(), product.drives(), product.trashCount(),
    view === 'trash' || query ? Promise.resolve(0) : product.size(viewOf()),
  ]);
  if (!pages.isCurrent(generation) || read().view !== view
      || JSON.stringify(viewOf()) !== key || read().query !== query) return;
  await pages.refresh(view === 'trash' ? trashCount : query ? read().total : size);
  if (!pages.isCurrent(generation)) return;
  update({ counts: { ...counts, trash: trashCount }, drives });
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
    const result = await product.forgetMissing(folder || '');
    await Promise.all([loadView(), loadFolders()]);
    notify(`${result.forgotten} missing photograph${result.forgotten === 1 ? '' : 's'} forgotten. They come back, with their decisions, if the files ever do.`);
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

async function synchronizeFolder(folder) {
  // Lightroom's Synchronize: walk this folder on every drive that is here,
  // now. The following loop does the same for the working drives each minute.
  folderMenu.hidden = true;
  notify(`Synchronizing ${folder || 'everything'}…`);
  try {
    const results = await product.synchronize(folder || '');
    const added = results.reduce((n, r) => n + (r.photos_added || 0), 0);
    const moved = results.reduce((n, r) => n + (r.photos_moved || 0), 0);
    const retired = results.reduce((n, r) => n + (r.copies_retired || 0), 0);
    await Promise.all([refreshInPlace(), loadFolders()]);
    notify(`Synchronized: ${added} added, ${moved} moved, ${retired} no longer there.`);
  } catch (error) {
    notify(error.message);
  }
}

document.addEventListener('contextmenu', (event) => {
  const row = event.target.closest('.folder-row');
  const all = event.target.closest('.nav-row[data-action="all-photos"]');
  if (!row && !all) {
    folderMenu.hidden = true;
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

function showFolder(path) {
  if (read().query) {
    searchBox.value = '';
    update({ query: '' });
  }
  if (read().view === 'refine') {
    update({ folder: path, collection: null, selected: null, selectedIndex: null, marked: new Set() });
    refineWorkflow.resize(refineWorkflow.size());
    return;
  }
  update({ view: 'library', folder: path, collection: null,
           selected: null, selectedIndex: null, marked: new Set() });
  workspace.scrollTo({ top: 0 });
  loadView();
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

async function selectIndex(index, { open = loupe.open } = {}) {
  if (read().total === 0) {
    update({ selected: null, selectedIndex: null });
    return;
  }
  const bounded = Math.max(0, Math.min(read().total - 1, index));
  if (!Number.isFinite(bounded)) return;
  if (!read().photos.has(bounded)) await pages.ensure(bounded);
  const photo = read().photos.get(bounded);
  if (!photo) return;
  scrollIndexIntoView(bounded);
  selectPhoto(bounded);
  scheduleGrid();
  if (!loupe.open) requestAnimationFrame(() => grid.querySelector(`[data-index="${bounded}"]`)?.focus());
  if (open) showPhoto(photo);
}

function showPhoto(photo) {
  // The loupe shows the best picture the row has: the loupe tile, else the
  // grid tile scaled up while the loupe tile is made first (the library is
  // told this photograph is what is being looked at). When the row changes
  // under an open loupe, `renderLoupe` swaps the picture in.
  product.look([photo.id]).catch(() => {});
  renderLoupe(photo);
  if (!loupe.open) loupe.showModal();
}

function renderLoupe(photo) {
  loupeImage.dataset.turn = photo.rotate || 0;
  const source = photo.loupe || photo.tile || '';
  const note = document.querySelector('[data-loupe-note]');
  note.textContent = source ? ''
    : photo.tile_failed || photo.loupe_failed ? 'This photograph cannot be shown.'
      : photo.reachable ? 'Preparing this photograph…'
        : 'The drive that holds this photograph is away.';
  note.hidden = Boolean(source);
  if (loupeImage.dataset.source === source) return;
  loupeImage.dataset.source = source;
  if (source) loupeImage.src = source;
  else loupeImage.removeAttribute('src');
  loupeImage.alt = photo.tail || 'Selected photo';
}

function openPhoto(index) {
  if (read().selectedIndex !== index) selectPhoto(index);
  const photo = read().photos.get(index);
  if (photo) showPhoto(photo);
}


function renderFolders(state) {
  // One row per visible node, depth as a CSS variable; a node opens from its
  // disclosure and scopes the grid from its name. Safety is one quiet mark:
  // amber when something under here exists only on the working disk, hollow
  // when the record drive is away and nobody can say, nothing when all is well.
  const rows = [];
  // A hollow ring says "the record drive is away, so nobody can say"; with no
  // record drive registered at all there is nothing to say per folder.
  const anyRecord = state.drives.some((drive) => drive.is_record);
  const walk = (nodes, depth) => {
    for (const node of nodes) {
      const row = document.createElement('div');
      row.className = 'folder-row' + (state.view === 'library' && state.folder === node.path ? ' is-active' : '');
      row.style.setProperty('--depth', depth);
      row.dataset.folder = node.path;
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
  if (loupe.open && state.selected) renderLoupe(state.selected);
  const count = state.counts.photos.toLocaleString();
  document.querySelector('[data-photo-count]').textContent = `${state.total.toLocaleString()} photos`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-trash-count]').textContent = state.counts.trash.toLocaleString();
  document.querySelector('[data-result-label]').textContent = state.loading
    ? 'Loading your library…'
    : `${state.photos.size.toLocaleString()} of ${state.total.toLocaleString()} loaded`;
  status.textContent = state.notice || (state.scanning
    ? 'Reading your photos…'
    : state.counts.unidentified
      ? `Reading ${state.counts.unidentified.toLocaleString()} photos…`
      : '');
  const searching = state.view === 'library' && Boolean(state.query);
  const refining = state.view === 'refine';
  grid.hidden = refining;
  document.querySelector('[data-refine]').hidden = !refining;
  document.querySelector('[data-refine-progress]').hidden = !refining;
  document.querySelector('[data-refine-size]').hidden = !refining;
  document.querySelector('[data-action="refine"]').hidden = state.view !== 'library' || searching;
  document.querySelector('[data-action="leave-refine"]').hidden = !refining;
  document.querySelector('[data-result-label]').hidden = refining;
  document.querySelector('[data-density]').closest('label').hidden = refining;
  document.querySelector('[data-sort]').closest('label').hidden = state.view === 'trash' || refining || searching;
  // A decision is keyed on identity, and identity arrives shortly after a
  // sweep; until then the photograph cannot take one, so nothing offers to.
  const canCull = state.view === 'library' && Boolean(state.selected?.hash);
  document.querySelector('[data-cull-actions]').hidden = !canCull;
  document.querySelector('[data-action="pick"]').hidden = !canCull || state.selected.status === 'picked';
  document.querySelector('[data-action="clear-pick"]').hidden = !canCull || state.selected.status !== 'picked';
  document.querySelector('[data-action="forget"]').hidden = !(state.view === 'library' && state.selected && state.selected.placed === 0);
  document.querySelector('[data-action="restore"]').hidden = state.view !== 'trash' || !state.selected;
  document.querySelector('[data-action="empty-trash"]').hidden = state.view !== 'trash' || !state.counts.trash;
  document.querySelector('.nav-row[data-action="all-photos"]').classList.toggle('is-active', state.view === 'library' && !state.folder);
  document.querySelector('.nav-row[data-action="trash-view"]').classList.toggle('is-active', state.view === 'trash');
  const shelf = (state.collections || []).find((c) => c.id === state.collection);
  document.querySelector('.view-title strong').textContent = state.view === 'trash'
    ? 'Trash'
    : searching ? `Results for “${state.query}”`
    : (refining ? 'Refine · ' : '')
      + (shelf ? shelf.name : state.folder ? state.folder.split('/').pop() : 'All photos');
  document.querySelector('[data-action="add-chip"]').hidden = state.view !== 'library' || refining;
  document.querySelector('[data-action="save-view"]').hidden =
    state.view !== 'library' || refining || searching || !viewOf();
  document.querySelector('[data-action="keep-results"]').hidden = !searching;
  renderFolders(state);

  driveList.replaceChildren(...state.drives.map((drive) => {
    const row = document.createElement('div');
    row.className = 'drive-row';
    const light = document.createElement('span');
    light.className = drive.attached ? 'drive-light is-online' : 'drive-light';
    const name = document.createElement('span');
    name.textContent = drive.label || drive.root;
    row.append(light, name);
    return row;
  }));
}

function render(state) {
  if (state.view !== 'refine') visibleGrid();
  renderChrome(state);
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
    update({ view: 'library', folder: null, collection: null, chips: [], query: '',
             selected: null, selectedIndex: null, marked: new Set() });
    workspace.scrollTo({ top: 0 });
    loadView();
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
  if (action === 'close-empty') trashWorkflow.closeDialog();
  if (action === 'close-loupe') closeLoupe();
});

document.addEventListener('keydown', (event) => {
  const target = event.target;
  const isTyping = target.matches('input, select, textarea, [contenteditable="true"]');
  if (homeDialog.open) return;
  if (intakeWorkflow.isOpen()) {
    if (event.key === 'Escape') intakeWorkflow.close();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !isTyping) {
    undo.run();
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
    if (loupe.open) closeLoupe();
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
  if (refineWorkflow.isOpen()) {
    if (refineWorkflow.key(event)) event.preventDefault();
    return;
  }

  const current = read().selectedIndex;
  const key = event.key.toLowerCase();
  if (key === 'b' && read().view === 'library' && selection().length) {
    collectionsPanel.toss();
    event.preventDefault();
    return;
  }
  const cullActions = { p: 'pick', u: 'clear', x: 'reject', r: event.shiftKey ? 'turnLeft' : 'turnRight' };
  if (key in cullActions && read().view === 'library' && read().selected?.hash) {
    cullWorkflow.apply(cullActions[key]);
    event.preventDefault();
    return;
  }
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    const move = event.key === 'ArrowLeft' ? -1 : 1;
    selectIndex((current ?? (move > 0 ? -1 : read().total)) + move);
    event.preventDefault();
  } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    // Rows do not share columns, so up and down mean the nearest cell in the
    // row above or below, which the lens knows from its layout.
    const direction = event.key === 'ArrowUp' ? -1 : 1;
    const next = current === null ? (direction > 0 ? 0 : read().total - 1) : library.neighbour(current, direction);
    if (next !== null) selectIndex(next);
    event.preventDefault();
  } else if (event.key === 'Home') {
    selectIndex(0);
    event.preventDefault();
  } else if (event.key === 'End') {
    selectIndex(read().total - 1);
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
  collectionsPanel.menuFor(event);
});

workspace.addEventListener('scroll', scheduleGrid, { passive: true });
new ResizeObserver(scheduleGrid).observe(workspace);
loupe.addEventListener('click', (event) => {
  if (event.target === loupe) closeLoupe();
});
loupe.addEventListener('close', () => {
  loupeImage.removeAttribute('src');
  delete loupeImage.dataset.source;
  loupeImage.alt = '';
});

subscribe(render);
product.home().then((where) => (where
  ? Promise.all([loadView(), loadFolders(), collectionsPanel.refresh()])
  : chooseHome()));
followLibrary();
