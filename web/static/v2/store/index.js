const state = {
  chips: [],
  // Covers the person has opened: their members sit in the grid after them.
  // Stacks open by default: `folded` holds the covers closed while open,
  // `expanded` the covers opened while collapsed.
  collapsed: false,
  expanded: new Set(),
  folded: new Set(),
  album: null,
  albums: [],
  people: [],
  labels: [],
  counts: { photos: 0, starred: 0, unidentified: 0, trash: 0 },
  drives: [],
  folders: [],
  marked: new Set(),
  panels: { left: true, right: true, top: true },
  tree: [],
  open: new Set(),
  photos: new Map(),
  query: '',
  like: [],
  days: [],
  importing: '',
  total: 0,
  loading: true,
  scanning: false,
  selected: null,
  selectedIndex: null,
  sort: 'newest',
  view: 'library',
};

const listeners = new Set();
const lenses = new Map();

export function read() {
  return state;
}

export function update(patch) {
  Object.assign(state, patch);
  for (const listener of listeners) listener(state);
}

export function subscribe(listener) {
  listeners.add(listener);
  listener(state);
  return () => listeners.delete(listener);
}

export function registerLens(name, lens) {
  if (lenses.has(name)) throw new Error(`Lens already registered: ${name}`);
  lenses.set(name, Object.freeze(lens));
}

export function getLens(name) {
  const lens = lenses.get(name);
  if (!lens) throw new Error(`Lens not registered: ${name}`);
  return lens;
}
