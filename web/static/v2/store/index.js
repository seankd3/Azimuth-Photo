const state = {
  counts: { photos: 0, starred: 0, unidentified: 0 },
  drives: [],
  photos: [],
  loading: true,
  scanning: false,
  selected: null,
  sort: 'newest',
  offset: 0,
  exhausted: false,
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
