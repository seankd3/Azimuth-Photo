import { registerLens } from '../store/index.js';

const rendered = new WeakMap();
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

function skeletons(grid) {
  replaceGrid(grid);
  for (let index = 0; index < 18; index += 1) {
    const skeleton = element('div', 'photo-skeleton');
    skeleton.style.aspectRatio = index % 4 === 0 ? '4 / 5' : '3 / 2';
    grid.append(skeleton);
  }
}

function replaceGrid(grid, ...children) {
  if (tileObserver) {
    for (const image of grid.querySelectorAll('img')) tileObserver.unobserve(image);
  }
  grid.replaceChildren(...children);
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

function emptyState(onAdd) {
  const empty = element('div', 'empty-state');
  const mark = element('div', 'empty-mark');
  mark.setAttribute('aria-hidden', 'true');
  mark.append(element('i'), element('i'), element('i'));
  empty.append(mark, element('h2', '', 'No photos here yet.'));
  empty.append(element('p', '', 'Add a folder to start your library.'));
  const button = element('button', 'primary-button', 'Add a folder');
  button.type = 'button';
  button.addEventListener('click', onAdd);
  empty.append(button);
  return empty;
}

function photoCell(photo, selected, actions) {
  const cell = element('button', 'photo-cell');
  cell.type = 'button';
  cell.dataset.photoId = photo.id;
  cell.classList.toggle('is-selected', selected === photo.id);
  cell.setAttribute('aria-label', photo.tail || `Photo ${photo.id}`);

  const image = element('img');
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  cell.append(image);
  loadTile(image, photo, actions);
  cell.addEventListener('click', () => actions.select(photo));
  cell.addEventListener('dblclick', () => actions.open(photo));
  return cell;
}

function renderGrid(grid, state, actions) {
  grid.style.setProperty('--cell-size', `${actions.cellSize}px`);
  grid.setAttribute('aria-busy', String(state.loading));
  if (state.loading && state.photos.length === 0) {
    rendered.delete(grid);
    skeletons(grid);
    return;
  }
  if (state.photos.length === 0) {
    rendered.delete(grid);
    replaceGrid(grid, emptyState(actions.addDrive));
    return;
  }

  const signature = state.photos.map((photo) => `${photo.id}:${photo.tail}`).join('\n');
  if (rendered.get(grid) !== signature) {
    replaceGrid(grid, ...state.photos.map((photo) => photoCell(photo, null, actions)));
    rendered.set(grid, signature);
  }
  const selected = String(state.selected?.id ?? '');
  for (const cell of grid.children) {
    cell.classList.toggle('is-selected', cell.dataset.photoId === selected);
  }
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
