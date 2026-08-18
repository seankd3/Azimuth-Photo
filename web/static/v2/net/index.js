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
  counts: () => invoke('counts'),
  drives: () => invoke('drives'),
  photos: ({ sort, limit, offset }) => invoke('photos', sort, limit, offset),
  photo: (id) => invoke('photo', id),
  tile: (id, size = 400) => invoke('tile', id, size),
  attach: (root, isRecord) => invoke('attach', root, isRecord),
  refresh: (uuid) => invoke('refresh', uuid),
  chooseFolder: () => invoke('choose_folder'),
});
