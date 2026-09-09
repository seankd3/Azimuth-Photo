const ready = new Promise((resolve) => {
  if (window.pywebview?.api) resolve(window.pywebview.api);
  else window.addEventListener('pywebviewready', () => resolve(window.pywebview.api), { once: true });
});

async function invoke(method, ...arguments_) {
  const bridge = await ready;
  try {
    return await bridge[method](...arguments_);
  } catch (error) {
    throw new Error(error?.message || 'Azimuth could not complete that.');
  }
}

export const library = Object.freeze({
  home: () => invoke('home'),
  proposeHome: () => invoke('propose_home'),
  settleHome: (path) => invoke('settle_home', path),
  counts: () => invoke('counts'),
  drives: () => invoke('drives'),
  pulse: () => invoke('pulse'),
  look: (ids) => invoke('look', ids),
  photos: ({ sort, limit, offset, view = null }) => invoke('photos', sort, limit, offset, view),
  size: (view = null) => invoke('size', view),
  folders: () => invoke('folders'),
  photo: (id) => invoke('photo', id),
  trashPhotos: ({ limit, offset }) => invoke('trash_photos', limit, offset),
  trashCount: () => invoke('trash_count'),
  pick: (ids) => invoke('pick', ids),
  clearPick: (ids) => invoke('clear_pick', ids),
  reject: (ids) => invoke('reject', ids),
  restore: (ids) => invoke('restore', ids),
  undoCull: (changes) => invoke('undo_cull', changes),
  turn: (ids, by = 90) => invoke('turn', ids, by),
  forget: (ids) => invoke('forget', ids),
  find: ({ query, limit, offset, view = null, like = [] }) => invoke('search', query, limit, offset, view, like),
  days: (view = null) => invoke('days', view),
  sessions: () => invoke('sessions'),
  develop: (id, patch) => invoke('develop', id, patch),
  developPreview: (id, patch, size = 1280) => invoke('develop_preview', id, patch, size),
  developState: (id) => invoke('develop_state', id),
  exportSettings: (ids) => invoke('export_settings', ids),
  rank: ({ n, view = null, avoid = [], mode = 'learn' }) => invoke('rank', n, view, avoid, mode),
  albums: () => invoke('albums'),
  createAlbum: (name, chips = null) => invoke('create_album', name, chips),
  renameAlbum: (id, name) => invoke('rename_album', id, name),
  forgetAlbum: (id) => invoke('forget_album', id),
  rememberAlbum: (id) => invoke('remember_album', id),
  addToAlbum: (id, ids) => invoke('add_to_album', id, ids),
  removeFromAlbum: (id, ids) => invoke('remove_from_album', id, ids),
  quick: (ids) => invoke('quick', ids),
  freezeAlbum: (id) => invoke('freeze_album', id),
  saveView: (name, view) => invoke('save_view', name, view),
  savePhotos: (name, ids) => invoke('save_photos', name, ids),
  cameras: () => invoke('cameras'),
  facets: () => invoke('facets'),
  people: () => invoke('people'),
  labels: () => invoke('labels'),
  teach: (word, ids, yes) => invoke('teach', word, ids, yes),
  namePerson: (exemplar, name) => invoke('name_person', exemplar, name),
  round: (winnerId, overIds) => invoke('round', winnerId, overIds),
  unround: (decision) => invoke('unround', decision),
  forgetMissing: (folder = '', dry = false) => invoke('forget_missing', folder, dry),
  adoptTrack: () => invoke('adopt_track'),
  identifiers: (view = null, trashed = false) => invoke('identifiers', view, trashed),
  position: (id, sort, view = null) => invoke('position', id, sort, view),
  exportFolder: (folder) => invoke('export_settings', null, folder),
  exportPhotos: (ids, quality = 92, longEdge = 0, rename = '') => invoke('export_photos', ids, quality, longEdge, rename),
  cards: () => invoke('cards'),
  stage: (source) => invoke('stage', source),
  bring: (source, keys, kind, clearSource = false, roll = '', rolls = {}) => invoke('bring', source, keys, kind, clearSource, roll, rolls),
  intakeStatus: () => invoke('intake_status'),
  stopIntake: () => invoke('stop_intake'),
  thumb: (source, key) => invoke('thumb', source, key),
  synchronize: (folder = '') => invoke('synchronize', folder),
  emptyTrash: (expectedCount, dryRun = false) => invoke('empty_trash', expectedCount, dryRun),
  attach: (root, isRecord) => invoke('attach', root, isRecord),
  refresh: (uuid) => invoke('refresh', uuid),
  chooseFolder: () => invoke('choose_folder'),
});
