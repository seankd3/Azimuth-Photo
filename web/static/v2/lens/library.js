import { paths } from '../net/index.js';
import { registerLens } from '../store/index.js';

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function skeletons(grid) {
  grid.replaceChildren();
  for (let index = 0; index < 18; index += 1) {
    const skeleton = element('div', 'photo-skeleton');
    skeleton.style.aspectRatio = index % 4 === 0 ? '4 / 5' : '3 / 2';
    grid.append(skeleton);
  }
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

function photoCell(photo, selected, onSelect, onOpen) {
  const cell = element('button', 'photo-cell');
  cell.type = 'button';
  cell.dataset.photoId = photo.id;
  cell.classList.toggle('is-selected', selected === photo.id);
  cell.setAttribute('aria-label', photo.tail || `Photo ${photo.id}`);

  const image = element('img');
  image.src = paths.tile(photo.id);
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  cell.append(image);
  cell.addEventListener('click', () => onSelect(photo));
  cell.addEventListener('dblclick', () => onOpen(photo));
  return cell;
}

function renderGrid(grid, state, actions) {
  grid.style.setProperty('--cell-size', `${actions.cellSize}px`);
  grid.setAttribute('aria-busy', String(state.loading));
  if (state.loading && state.photos.length === 0) {
    skeletons(grid);
    return;
  }
  if (state.photos.length === 0) {
    grid.replaceChildren(emptyState(actions.addDrive));
    return;
  }
  grid.replaceChildren(...state.photos.map((photo) => photoCell(
    photo,
    state.selected && state.selected.id,
    actions.select,
    actions.open,
  )));
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
