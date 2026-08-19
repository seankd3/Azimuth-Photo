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
  photos: ({ sort, limit, offset, folder = null }) => invoke('photos', sort, limit, offset, folder),
  size: (folder = null) => invoke('size', folder),
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
  forgetMissing: (folder = '') => invoke('forget_missing', folder),
  cards: () => invoke('cards'),
  stage: (source) => invoke('stage', source),
  bring: (source, keys, kind, clearSource = false, roll = '') => invoke('bring', source, keys, kind, clearSource, roll),
  intakeStatus: () => invoke('intake_status'),
  stopIntake: () => invoke('stop_intake'),
  thumb: (source, key) => invoke('thumb', source, key),
  synchronize: (folder = '') => invoke('synchronize', folder),
  emptyTrash: (expectedCount, dryRun = false) => invoke('empty_trash', expectedCount, dryRun),
  attach: (root, isRecord) => invoke('attach', root, isRecord),
  refresh: (uuid) => invoke('refresh', uuid),
  chooseFolder: () => invoke('choose_folder'),
});
