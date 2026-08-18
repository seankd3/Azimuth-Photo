import { library as product } from '../net/index.js';
import { getLens, read, subscribe, update } from '../store/index.js';

const PAGE = 200;
const library = getLens('library');
const grid = document.querySelector('[data-grid]');
const inspector = document.querySelector('[data-inspector]');
const driveDialog = document.querySelector('[data-drive-dialog]');
const driveForm = document.querySelector('[data-drive-form]');
const loupe = document.querySelector('[data-loupe]');
const loupeImage = document.querySelector('[data-loupe-image]');
const loadMore = document.querySelector('[data-load-more]');
const status = document.querySelector('[data-status]');
let cellSize = 220;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function openDriveDialog() {
  driveDialog.showModal();
  driveForm.querySelector('[type="submit"]').focus();
}

function closeDriveDialog() {
  driveDialog.close();
  driveForm.reset();
  document.querySelector('[data-drive-error]').textContent = '';
}

async function loadLibrary({ append = false } = {}) {
  const state = read();
  const offset = append ? state.photos.length : 0;
  if (!append) update({ loading: true });
  try {
    const [counts, drives, photos] = await Promise.all([
      product.counts(),
      product.drives(),
      product.photos({ sort: state.sort, limit: PAGE, offset }),
    ]);
    update({
      counts,
      drives,
      photos: append ? [...state.photos, ...photos] : photos,
      loading: false,
      exhausted: photos.length < PAGE,
    });
    if (!read().scanning) status.textContent = '';
    if (!drives.length && !photos.length) openDriveDialog();
  } catch (error) {
    update({ loading: false });
    status.textContent = error.message;
  }
}

async function scanDrive(drive) {
  update({ scanning: true });
  try {
    let finished = false;
    const scan = product.refresh(drive.uuid).finally(() => {
      finished = true;
    });
    while (!finished) {
      await delay(500);
      await loadLibrary();
    }
    const result = await scan;
    await loadLibrary();
    status.textContent = result.applied
      ? `${result.photos_added.toLocaleString()} photos added.`
      : result.reason || 'The folder could not be fully read.';
  } catch (error) {
    status.textContent = error.message;
    throw error;
  } finally {
    update({ scanning: false });
  }
}

async function selectPhoto(photo) {
  update({ selected: photo });
  try {
    const details = await product.photo(photo.id);
    update({ selected: { ...photo, ...details } });
  } catch (error) {
    status.textContent = error.message;
  }
}

async function openPhoto(photo) {
  loupeImage.src = await product.tile(photo.id, 1920);
  loupeImage.alt = photo.tail || 'Selected photo';
  loupe.showModal();
}

function render(state) {
  library.renderGrid(grid, state, {
    addDrive: openDriveDialog,
    select: selectPhoto,
    open: openPhoto,
    tile: product.tile,
    cellSize,
  });
  library.renderInspector(inspector, state.selected);
  const count = state.counts.photos.toLocaleString();
  document.querySelector('[data-photo-count]').textContent = `${count} photos`;
  document.querySelector('[data-sidebar-count]').textContent = count;
  document.querySelector('[data-result-label]').textContent = state.loading
    ? 'Loading your library…'
    : `${state.photos.length.toLocaleString()} of ${count}`;
  status.textContent = state.scanning ? 'Reading your photos…' : status.textContent;
  loadMore.hidden = state.loading || state.exhausted || state.photos.length === 0;

  const driveList = document.querySelector('[data-drive-list]');
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

driveForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = driveForm.querySelector('[type="submit"]');
  const error = document.querySelector('[data-drive-error]');
  submit.disabled = true;
  error.textContent = '';
  try {
    const root = await product.chooseFolder();
    if (!root) return;
    const drive = await product.attach(root, driveForm.elements.is_record.checked);
    closeDriveDialog();
    await scanDrive(drive);
  } catch (reason) {
    error.textContent = reason.message;
  } finally {
    submit.disabled = false;
  }
});

document.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'add-drive') openDriveDialog();
  if (action === 'all-photos') {
    update({ selected: null, photos: [], exhausted: false });
    document.querySelector('.workspace').scrollTo({ top: 0 });
    loadLibrary();
  }
  if (action === 'close-drive') closeDriveDialog();
  if (action === 'close-loupe') loupe.close();
});

document.querySelector('[data-sort]').addEventListener('change', (event) => {
  update({ sort: event.target.value, photos: [], exhausted: false });
  loadLibrary();
});

document.querySelector('[data-density]').addEventListener('input', (event) => {
  cellSize = Number(event.target.value);
  render(read());
});

loadMore.addEventListener('click', () => loadLibrary({ append: true }));
loupe.addEventListener('click', (event) => {
  if (event.target === loupe) loupe.close();
});

subscribe(render);
loadLibrary();
