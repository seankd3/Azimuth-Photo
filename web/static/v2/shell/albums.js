// Albums in the sidebar and the verbs around them. One primitive worn
// two ways: plain albums hold what you put in them (B, right-click,
// drag); smart ones hold whatever their chips match, and Freeze turns the
// second into the first — the album you are about to share elsewhere. The
// shelf is the name: America/Utah sits under America, and a parent browses
// as the union of what is under it.

export function createAlbumsPanel({ product, read, update, notify, reload, moved, selection, describe, viewOf }) {
  const tree = document.querySelector('[data-albums-tree]');
  const menu = document.querySelector('[data-album-menu]');
  const photoMenu = document.querySelector('[data-photo-menu]');
  const namePop = document.querySelector('[data-name-pop]');
  const nameInput = namePop.querySelector('[data-name-input]');
  let askName = null;   // resolver while the name popover is open

  async function refresh() {
    try {
      update({ albums: await product.albums() });
    } catch (error) {
      notify(error.message);
    }
  }

  function show(id) {
    // The view moves; whichever stage is up follows it. Refine re-scopes in
    // place, the grid reloads — one rule for folders, albums and chips.
    if (read().view === 'rank') update({ album: id, folder: null });
    else update({ view: 'library', album: id, folder: null, selected: null, selectedIndex: null });
    moved();
  }

  // ---- the tree: pinned first, then the names as a shelf ----

  let treeSeen = null;
  let treeActive = null;
  function render(state) {
    // Rebuilt only when the answer or the active row moved — not on every
    // store beat, which would blink hovers and drop clicks mid-swap.
    if (treeSeen === state.albums && treeActive === state.album) return;
    treeSeen = state.albums;
    treeActive = state.album;
    const held = state.albums || [];
    // Quick album is the standing target for B; Previous import only
    // means something once an import has happened.
    const pinned = ['quick', 'last-import']
      .map((id) => held.find((c) => c.id === id))
      .filter(Boolean)
      .filter((entry) => entry.id !== 'last-import' || entry.count);
    const named = held.filter((c) => !c.pinned)
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    const rows = [];
    const place = (entry, depth, leaf) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row' + (state.album === entry.id ? ' is-active' : '');
      row.dataset.album = entry.id;
      row.dataset.smart = entry.smart ? '1' : '0';
      row.title = entry.name;
      row.style.paddingLeft = `${10 + depth * 14}px`;
      const name = document.createElement('span');
      name.className = 'leaf';
      name.textContent = leaf;
      row.append(name);
      if (entry.smart) {
        const mark = document.createElement('span');
        mark.className = 'smart-mark';
        mark.textContent = '◇';
        mark.title = 'Smart — fills itself from its filters';
        row.append(mark);
      }
      const count = document.createElement('span');
      count.className = 'set-count';
      count.textContent = entry.count.toLocaleString();
      row.append(count);
      rows.push(row);
    };
    for (const entry of pinned) place(entry, 0, entry.name);
    for (const entry of named) {
      const parts = entry.name.split('/');
      place(entry, parts.length - 1, parts[parts.length - 1]);
    }
    tree.replaceChildren(...rows);
  }

  // ---- naming things: one small popover, one question ----

  const nameOk = namePop.querySelector('[data-action="name-ok"]');
  let namedFrom = null;   // where focus goes back when the popover closes
  let justOpened = false; // the click that opened it bubbles to document once

  function prompt(title, anchor, initial = '') {
    // The anchor is a node or a rect captured before its menu was hidden —
    // a hidden node measures at the corner of the window.
    askName?.(null);
    return new Promise((resolve) => {
      askName = resolve;
      namedFrom = anchor instanceof Element ? anchor : null;
      justOpened = true;
      namePop.querySelector('[data-name-title]').textContent = title;
      nameInput.value = initial;
      nameOk.disabled = !initial.trim();
      const at = (anchor instanceof Element ? anchor.getBoundingClientRect() : anchor)
        || { left: window.innerWidth / 2 - 120, top: 74, bottom: 80 };
      namePop.hidden = false;
      namePop.style.left = `${Math.max(12, Math.min(at.left, window.innerWidth - namePop.offsetWidth - 12))}px`;
      const below = at.bottom + 6;
      const fits = below + namePop.offsetHeight + 12 <= window.innerHeight;
      namePop.style.top = `${fits ? below : Math.max(12, at.top - namePop.offsetHeight - 6)}px`;
      nameInput.focus();
      nameInput.select();
    });
  }

  function answer(value) {
    namePop.hidden = true;
    const resolve = askName;
    askName = null;
    if (namedFrom?.isConnected) namedFrom.focus();
    namedFrom = null;
    resolve?.(value);
  }

  nameInput.addEventListener('input', () => { nameOk.disabled = !nameInput.value.trim(); });
  nameOk.addEventListener('click', () => answer(nameInput.value.trim() || null));
  nameInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      // An empty name is not an answer; the field simply waits.
      if (nameInput.value.trim()) answer(nameInput.value.trim());
      event.preventDefault();
    }
    if (event.key === 'Escape') { answer(null); event.preventDefault(); event.stopPropagation(); }
  });

  // ---- verbs ----

  async function create(anchor) {
    const name = await prompt('New album', anchor);
    if (!name) return;
    try {
      await product.createAlbum(name);
      await refresh();
    } catch (error) {
      notify(error.message);
    }
  }

  async function saveView(anchor) {
    // The chips already say what this view is; the name box opens saying it.
    const name = await prompt('Save this view as', anchor, describe(read()));
    if (!name) return;
    try {
      await product.saveView(name, viewOf());
      await refresh();
      notify(`“${name}” keeps these filters, live.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function keepResults(anchor) {
    const name = await prompt('Save these results as', anchor, read().query);
    if (!name) return;
    try {
      const answered = await product.find({
        query: read().query, like: read().like || [], limit: 1000, offset: 0, view: viewOf() });
      const ids = answered.photos.map((p) => p.id);
      const kept = await product.savePhotos(name, ids);
      await refresh();
      notify(answered.total > kept.kept
        ? `“${name}” keeps the top ${kept.kept.toLocaleString()} of ${answered.total.toLocaleString()} results.`
        : `“${name}” keeps ${kept.kept.toLocaleString()} photographs from this search.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function quickToss(ids) {
    try {
      const moved = await product.quick(ids);
      await refresh();
      if (read().album === 'quick') await reload();
      notify('added' in moved
        ? `${moved.added} in Quick album — ${moved.count} held.`
        : `${moved.removed} out of Quick album — ${moved.count} held.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function toss(ids = selection()) {
    if (!ids.length) return;
    // The album being viewed is the target — Lightroom's target collection
    // without the set-as-target state. Inside one, everything shown is a
    // member, so B is the prune: out of a plain album, excluded from a
    // smart one. In Last import (a record, not a shelf) and everywhere
    // else, B fills the Quick album; inside Quick that same fill toggles.
    const here = (read().albums || []).find((c) => c.id === read().album);
    if (!here || here.id === 'quick' || here.id === 'last-import') return quickToss(ids);
    try {
      const out = await product.removeFromAlbum(here.id, ids);
      await refresh();
      await reload();
      notify(here.smart
        ? `${out.removed} excluded from “${here.name}”.`
        : `${out.removed} out of “${here.name}”.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function addTo(id, ids) {
    try {
      const added = await product.addToAlbum(id, ids);
      await refresh();
      const name = (read().albums.find((c) => c.id === id) || {}).name || 'the album';
      notify(`${added.added} added to “${name}”.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function removeFrom(id, ids) {
    try {
      await product.removeFromAlbum(id, ids);
      await refresh();
      await reload();
    } catch (error) {
      notify(error.message);
    }
  }

  // ---- the sidebar's own clicks, menu, and drops ----

  tree.addEventListener('click', (event) => {
    const row = event.target.closest('[data-album]');
    if (row) show(row.dataset.album);
  });

  tree.addEventListener('contextmenu', (event) => {
    const row = event.target.closest('[data-album]');
    if (!row) return;
    event.preventDefault();
    menu.dataset.album = row.dataset.album;
    menu.querySelector('[data-action="freeze-album"]').hidden = row.dataset.smart !== '1';
    const pinned = ['quick', 'last-import'].includes(row.dataset.album);
    menu.querySelector('[data-action="delete-album"]').hidden = pinned;
    menu.querySelector('[data-action="rename-album"]').hidden = pinned;
    menu.hidden = false;
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;
  });

  menu.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    const id = menu.dataset.album;
    menu.hidden = true;
    if (!action || !id) return;
    try {
      if (action === 'rename-album') {
        const current = (read().albums.find((c) => c.id === id) || {}).name || '';
        const row = tree.querySelector(`[data-album="${id}"]`);
        const name = await prompt('Rename to', row || tree, current);
        if (name) {
          const renamed = await product.renameAlbum(id, name);
          if (renamed?.followed) notify(`Renamed. ${renamed.followed} album${renamed.followed === 1 ? '' : 's'} under it followed.`);
        }
      }
      if (action === 'freeze-album') {
        const frozen = await product.freezeAlbum(id);
        notify(`Frozen — ${frozen.frozen.toLocaleString()} photographs are now yours to edit by hand.`);
      }
      if (action === 'delete-album') {
        await product.forgetAlbum(id);
        if (read().album === id) {
          update({ album: null });
          await reload();
        }
      }
      await refresh();
    } catch (error) {
      notify(error.message);
    }
  });

  tree.addEventListener('dragover', (event) => {
    const row = event.target.closest('[data-album]');
    if (!row) return;
    // Smart albums take the drop too: what you drag in is pinned in past
    // the rules — the exception the pro tools never had.
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    row.classList.add('is-drop');
  });
  tree.addEventListener('dragleave', (event) => {
    const row = event.target.closest('[data-album]');
    // Crossing between a row's own children fires dragleave too.
    if (row && !row.contains(event.relatedTarget)) row.classList.remove('is-drop', 'is-refused');
  });
  tree.addEventListener('drop', (event) => {
    const row = event.target.closest('[data-album]');
    if (!row) return;
    event.preventDefault();
    row.classList.remove('is-drop', 'is-refused');
    let ids = [];
    try {
      ids = JSON.parse(event.dataTransfer.getData('text/azimuth-ids') || '[]');
    } catch { ids = []; }
    if (ids.length) void addTo(row.dataset.album, ids);
  });

  // ---- the photograph's menu: built for the moment it opens ----

  function menuFor(event, verbs = []) {
    const ids = selection();
    if (!ids.length) return;
    event.preventDefault();
    // Where the mouse asked; menus hide before their verbs run, so the spot
    // is kept as a rect rather than measured off a hidden node.
    const spot = { left: event.clientX, top: event.clientY, bottom: event.clientY };
    const fixed = (read().albums || []).filter((c) => !c.smart && c.id !== 'last-import');
    const item = (label, run) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => { photoMenu.hidden = true; void run(); });
      return button;
    };
    const here = read().album;
    const viewing = (read().albums || []).find((c) => c.id === here);
    // B belongs to whichever verb it drives here: the prune inside an
    // album, the Quick toss everywhere else.
    const pruning = viewing && here !== 'quick' && here !== 'last-import';
    const rows = verbs.map(({ label, run }) => item(label, run));
    rows.push(item(pruning ? 'Quick album' : 'Quick album — B', () => quickToss(ids)));
    for (const entry of fixed.filter((c) => c.id !== 'quick')) {
      rows.push(item(`Add to “${entry.name}”`, () => addTo(entry.id, ids)));
    }
    rows.push(item('New album from selection…', async () => {
      const name = await prompt('New album from selection', spot);
      if (!name) return;
      const kept = await product.savePhotos(name, ids).catch((error) => { notify(error.message); return null; });
      if (kept) { await refresh(); notify(`“${name}” keeps ${kept.kept} photographs.`); }
    }));
    if (viewing) {
      rows.push(item(
        (viewing.smart ? `Exclude from “${viewing.name}”` : `Remove from “${viewing.name}”`)
          + (pruning ? ' — B' : ''),
        () => removeFrom(here, ids)));
    }
    photoMenu.replaceChildren(...rows);
    photoMenu.hidden = false;
    photoMenu.style.left = `${event.clientX}px`;
    photoMenu.style.top = `${event.clientY}px`;
  }

  document.addEventListener('click', (event) => {
    if (!menu.hidden && !menu.contains(event.target)) menu.hidden = true;
    if (!photoMenu.hidden && !photoMenu.contains(event.target)) photoMenu.hidden = true;
    // The click that just opened the popover bubbles here in the same
    // dispatch; it must not also be the click that closes it. Every later
    // outside click answers "no", whatever else it goes on to do.
    if (justOpened) { justOpened = false; return; }
    if (!namePop.hidden && !namePop.contains(event.target)) answer(null);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || (menu.hidden && photoMenu.hidden && namePop.hidden)) return;
    menu.hidden = true;
    photoMenu.hidden = true;
    if (!namePop.hidden) answer(null);
    event.preventDefault();
    event.stopImmediatePropagation();
  });

  return Object.freeze({ refresh, render, show, create, saveView, keepResults, toss, menuFor, ask: prompt });
}
