import { library as product } from '../net/index.js';
import { PageCache } from '../kit/page-cache.js';
import { getLens, read, subscribe, update } from '../store/index.js';
import { createCullWorkflow } from './cull.js';
import { createTrashWorkflow } from './trash.js';
import { createUndo } from './undo.js';

const PAGE = 200;
const CONTEXTBAR_HEIGHT = 46;
const library = getLens('library');
const workspace = document.querySelector('.workspace');
const grid = document.querySelector('[data-grid]');
const inspector = document.querySelector('[data-inspector]');
const driveDialog = document.querySelector('[data-drive-dialog]');
const driveForm = document.querySelector('[data-drive-form]');
const driveError = document.querySelector('[data-drive-error]');
const loupe = document.querySelector('[data-loupe]');
const loupeImage = document.querySelector('[data-loupe-image]');
const status = document.querySelector('[data-status]');
const driveList = document.querySelector('[data-drive-list]');

let cellSize = 220;
let scrollFrame = null;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const pages = new PageCache({
  pageSize: PAGE,
  load: (offset, limit) => read().view === 'trash'
    ? product.trashPhotos({ limit, offset })
    : product.photos({ sort: read().sort, limit, offset }),
  onPage: (photos, total) => update({ photos, total }),
  onError: (error) => {
    status.textContent = `Some photos could not be loaded. ${error.message}`;
  },
});
const undo = createUndo({
  product,
  reload: () => loadView(),
  status,
});
const trashWorkflow = createTrashWorkflow({
  product,
  read,
  reload: () => loadView(),
  status,
  undo,
});
const cullWorkflow = createCullWorkflow({
  product,
  read,
  replace: (photo) => pages.patch((item) => item.hash === photo.hash, (item) => ({ ...item, status: photo.status })),
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
  status,
  undo,
});

function visibleGrid() {
  const inTrash = read().view === 'trash';
  library.renderGrid(grid, read(), {
    emptyTitle: inTrash ? 'Trash is empty.' : 'No photos here yet.',
    emptyCopy: inTrash
      ? 'Rejected photographs stay recoverable here until you empty Trash.'
      : 'Add a folder to start your library.',
    emptyAction: inTrash ? null : { label: 'Add a folder', run: openDriveDialog },
    select: selectPhoto,
    open: openPhoto,
    tile: product.tile,
    need: (start, end) => pages.ensureRange(start, end),
    cellSize,
    scrollTop: workspace.scrollTop,
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
    const { sort, view } = read();
    const [counts, drives, trashCount, page] = await Promise.all([
      product.counts(),
      product.drives(),
      product.trashCount(),
      view === 'trash'
        ? product.trashPhotos({ limit: PAGE, offset: 0 })
        : product.photos({ sort, limit: PAGE, offset: 0 }),
    ]);
    if (!pages.isCurrent(requestGeneration) || read().view !== view) return;
    const total = view === 'trash' ? trashCount : counts.photos;
    pages.seed(requestGeneration, page, total);
    update({ counts: { ...counts, trash: trashCount }, drives, loading: false });
    if (!read().scanning) status.textContent = '';
    if (view === 'library' && !drives.length && !counts.photos) openDriveDialog();
  } catch (error) {
    if (!pages.isCurrent(requestGeneration)) return;
    update({ loading: false });
    status.textContent = error.message;
  }
}

async function scanDrive(drive) {
  update({ scanning: true });
  workspace.scrollTo({ top: 0 });
  try {
    let finished = false;
    const scan = product.refresh(drive.uuid).finally(() => { finished = true; });
    while (!finished) {
      await delay(500);
      await loadView();
    }
    const result = await scan;
    await loadView();
    status.textContent = result.applied
      ? `${result.photos_added.toLocaleString()} photos added.`
      : result.reason || 'The folder could not be fully read.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    update({ scanning: false });
  }
}

async function selectPhoto(photo, index) {
  update({ selected: photo, selectedIndex: index });
  try {
    const details = await product.photo(photo.id);
    if (read().selected?.id === photo.id) update({ selected: { ...photo, ...details } });
  } catch (error) {
    if (read().selected?.id === photo.id) status.textContent = error.message;
  }
}

function scrollIndexIntoView(index) {
  const columns = Number(grid.dataset.columns) || 1;
  const pitch = Number(grid.dataset.pitch) || cellSize;
  const cell = Number(grid.dataset.cell) || cellSize;
  const rowTop = 4 + (Math.floor(index / columns) * pitch);
  const viewport = Math.max(1, workspace.clientHeight - CONTEXTBAR_HEIGHT);
  if (rowTop < workspace.scrollTop) workspace.scrollTop = rowTop;
  else if (rowTop + cell > workspace.scrollTop + viewport) {
    workspace.scrollTop = rowTop + cell - viewport;
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
  selectPhoto(photo, bounded);
  scheduleGrid();
  if (!loupe.open) requestAnimationFrame(() => grid.querySelector(`[data-index="${bounded}"]`)?.focus());
  if (open) await showPhoto(photo);
}

async function showPhoto(photo) {
  const photoId = photo.id;
  try {
    const source = await product.tile(photoId, 1920);
    if (read().selected?.id !== photoId) return;
    loupeImage.src = source;
    loupeImage.alt = photo.tail || 'Selected photo';
    if (!loupe.open) loupe.showModal();
  } catch (error) {
    if (read().selected?.id === photoId) status.textContent = error.message;
  }
}

async function openPhoto(photo, index) {
  if (read().selectedIndex !== index) selectPhoto(photo, index);
  await showPhoto(photo);
}


function renderChrome(state) {
  library.renderInspector(inspector, state.selected);
  const count = state.counts.photos.toLocaleString();
  const viewCount = state.view === 'trash' ? state.counts.trash : state.counts.photos;
  document.querySelector('[data-photo-count]').textContent = `${viewCount.toLocaleString()} photos`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-trash-count]').textContent = state.counts.trash.toLocaleString();
  document.querySelector('[data-result-label]').textContent = state.loading
    ? 'Loading your library…'
    : `${state.photos.size.toLocaleString()} of ${state.total.toLocaleString()} loaded`;
  if (state.scanning) status.textContent = 'Reading your photos…';
  document.querySelector('[data-sort]').closest('label').hidden = state.view === 'trash';
  // A decision is keyed on identity, and identity arrives shortly after a
  // sweep; until then the photograph cannot take one, so nothing offers to.
  const canCull = state.view === 'library' && Boolean(state.selected?.hash);
  document.querySelector('[data-cull-actions]').hidden = !canCull;
  document.querySelector('[data-action="pick"]').hidden = !canCull || state.selected.status === 'picked';
  document.querySelector('[data-action="clear-pick"]').hidden = !canCull || state.selected.status !== 'picked';
  document.querySelector('[data-action="restore"]').hidden = state.view !== 'trash' || !state.selected;
  document.querySelector('[data-action="empty-trash"]').hidden = state.view !== 'trash' || !state.counts.trash;
  document.querySelector('.nav-row[data-action="all-photos"]').classList.toggle('is-active', state.view === 'library');
  document.querySelector('.nav-row[data-action="trash-view"]').classList.toggle('is-active', state.view === 'trash');
  document.querySelector('.view-title strong').textContent = state.view === 'trash' ? 'Trash' : 'All photos';

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
  visibleGrid();
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
  if (action === 'all-photos') {
    update({ view: 'library' });
    workspace.scrollTo({ top: 0 });
    loadView();
  }
  if (action === 'trash-view') {
    update({ view: 'trash' });
    workspace.scrollTo({ top: 0 });
    loadView();
  }
  if (action === 'restore') trashWorkflow.restoreSelected();
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
  if (event.key === 'Escape') {
    if (loupe.open) closeLoupe();
    else if (trashWorkflow.isOpen()) trashWorkflow.closeDialog();
    else if (driveDialog.open) closeDriveDialog();
    else if (read().selected) update({ selected: null, selectedIndex: null });
    else return;
    event.preventDefault();
    return;
  }
  if (isTyping || driveDialog.open || trashWorkflow.isOpen()) return;

  const current = read().selectedIndex;
  const key = event.key.toLowerCase();
  const cullActions = { p: 'pick', u: 'clear', x: 'reject' };
  if (key in cullActions && read().view === 'library' && read().selected?.hash) {
    cullWorkflow.apply(cullActions[key]);
    event.preventDefault();
    return;
  }
  const columns = Number(grid.dataset.columns) || 1;
  const moves = {
    ArrowLeft: -1,
    ArrowRight: 1,
    ArrowUp: -columns,
    ArrowDown: columns,
  };
  if (event.key in moves) {
    selectIndex((current ?? (moves[event.key] > 0 ? -1 : read().total)) + moves[event.key]);
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
  const anchor = read().selectedIndex ?? (Number(grid.dataset.start) || 0);
  cellSize = Number(event.target.value);
  visibleGrid();
  scrollIndexIntoView(anchor);
  visibleGrid();
});

workspace.addEventListener('scroll', scheduleGrid, { passive: true });
new ResizeObserver(scheduleGrid).observe(workspace);
loupe.addEventListener('click', (event) => {
  if (event.target === loupe) closeLoupe();
});
loupe.addEventListener('close', () => {
  loupeImage.removeAttribute('src');
  loupeImage.alt = '';
});

subscribe(render);
loadView();
