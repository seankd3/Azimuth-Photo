// Collections in the sidebar and the verbs around them. One primitive worn
// two ways: fixed collections hold what you put in them (B, right-click,
// drag); smart ones hold whatever their chips match, and Freeze turns the
// second into the first — the album you are about to share elsewhere. The
// shelf is the name: America/Utah sits under America, and a parent browses
// as the union of what is under it.

export function createCollectionsPanel({ product, read, update, notify, reload, selection, viewOf }) {
  const tree = document.querySelector('[data-collections-tree]');
  const menu = document.querySelector('[data-collection-menu]');
  const photoMenu = document.querySelector('[data-photo-menu]');
  const namePop = document.querySelector('[data-name-pop]');
  const nameInput = namePop.querySelector('[data-name-input]');
  let askName = null;   // resolver while the name popover is open

  async function refresh() {
    try {
      update({ collections: await product.collections() });
    } catch (error) {
      notify(error.message);
    }
  }

  function show(id) {
    update({ view: 'library', collection: id, folder: null, chips: read().chips,
             selected: null, selectedIndex: null, marked: new Set() });
    reload();
  }

  // ---- the tree: pinned first, then the names as a shelf ----

  function render(state) {
    const held = state.collections || [];
    const pinned = ['quick', 'last-import']
      .map((id) => held.find((c) => c.id === id)).filter(Boolean);
    const named = held.filter((c) => !c.pinned)
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    const rows = [];
    const place = (entry, depth, leaf) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'collection-row' + (state.collection === entry.id ? ' is-active' : '');
      row.dataset.collection = entry.id;
      row.dataset.smart = entry.smart ? '1' : '0';
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

  function prompt(title, anchor, initial = '') {
    return new Promise((resolve) => {
      askName = resolve;
      namePop.querySelector('[data-name-title]').textContent = title;
      nameInput.value = initial;
      const at = anchor?.getBoundingClientRect?.() || { left: window.innerWidth / 2 - 120, bottom: 80 };
      namePop.hidden = false;
      namePop.style.left = `${Math.min(at.left, window.innerWidth - 260)}px`;
      namePop.style.top = `${at.bottom + 6}px`;
      nameInput.focus();
      nameInput.select();
    });
  }

  function answer(value) {
    namePop.hidden = true;
    const resolve = askName;
    askName = null;
    resolve?.(value);
  }

  namePop.querySelector('[data-action="name-ok"]').addEventListener('click', () => answer(nameInput.value.trim() || null));
  nameInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { answer(nameInput.value.trim() || null); event.preventDefault(); }
    if (event.key === 'Escape') { answer(null); event.preventDefault(); event.stopPropagation(); }
  });

  // ---- verbs ----

  async function create(anchor) {
    const name = await prompt('New collection', anchor);
    if (!name) return;
    try {
      await product.createCollection(name);
      await refresh();
    } catch (error) {
      notify(error.message);
    }
  }

  async function saveView(anchor) {
    const name = await prompt('Save this view as', anchor);
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
    const name = await prompt('Save these results as', anchor);
    if (!name) return;
    try {
      const answered = await product.find({ query: read().query, limit: 500, offset: 0, view: viewOf() });
      const ids = answered.photos.map((p) => p.id);
      const kept = await product.savePhotos(name, ids);
      await refresh();
      notify(`“${name}” keeps ${kept.kept.toLocaleString()} photographs from this search.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function toss() {
    const ids = selection();
    if (!ids.length) return;
    try {
      const moved = await product.quick(ids);
      await refresh();
      if (read().collection === 'quick') await reload();
      notify('added' in moved
        ? `${moved.added} in Quick Collection — ${moved.count} held.`
        : `${moved.removed} out of Quick Collection — ${moved.count} held.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function addTo(id, ids) {
    try {
      const added = await product.addToCollection(id, ids);
      await refresh();
      const name = (read().collections.find((c) => c.id === id) || {}).name || 'the collection';
      notify(`${added.added} added to “${name}”.`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function removeFrom(id, ids) {
    try {
      await product.removeFromCollection(id, ids);
      await refresh();
      await reload();
    } catch (error) {
      notify(error.message);
    }
  }

  // ---- the sidebar's own clicks, menu, and drops ----

  tree.addEventListener('click', (event) => {
    const row = event.target.closest('[data-collection]');
    if (row) show(row.dataset.collection);
  });

  tree.addEventListener('contextmenu', (event) => {
    const row = event.target.closest('[data-collection]');
    if (!row) return;
    event.preventDefault();
    menu.dataset.collection = row.dataset.collection;
    menu.querySelector('[data-action="freeze-collection"]').hidden = row.dataset.smart !== '1';
    const pinned = ['quick', 'last-import'].includes(row.dataset.collection);
    menu.querySelector('[data-action="delete-collection"]').hidden = pinned;
    menu.querySelector('[data-action="rename-collection"]').hidden = pinned;
    menu.hidden = false;
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;
  });

  menu.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    const id = menu.dataset.collection;
    menu.hidden = true;
    if (!action || !id) return;
    try {
      if (action === 'rename-collection') {
        const current = (read().collections.find((c) => c.id === id) || {}).name || '';
        const name = await prompt('Rename to', tree, current);
        if (name) await product.renameCollection(id, name);
      }
      if (action === 'freeze-collection') {
        const frozen = await product.freezeCollection(id);
        notify(`Frozen — ${frozen.frozen.toLocaleString()} photographs are now yours to edit by hand.`);
      }
      if (action === 'delete-collection') {
        await product.forgetCollection(id);
        if (read().collection === id) {
          update({ collection: null });
          await reload();
        }
      }
      await refresh();
    } catch (error) {
      notify(error.message);
    }
  });

  tree.addEventListener('dragover', (event) => {
    const row = event.target.closest('[data-collection]');
    if (!row || row.dataset.smart === '1') return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    row.classList.add('is-drop');
  });
  tree.addEventListener('dragleave', (event) => {
    event.target.closest('[data-collection]')?.classList.remove('is-drop');
  });
  tree.addEventListener('drop', (event) => {
    const row = event.target.closest('[data-collection]');
    if (!row) return;
    event.preventDefault();
    row.classList.remove('is-drop');
    let ids = [];
    try {
      ids = JSON.parse(event.dataTransfer.getData('text/azimuth-ids') || '[]');
    } catch { ids = []; }
    if (ids.length) void addTo(row.dataset.collection, ids);
  });

  // ---- the photograph's menu: built for the moment it opens ----

  function menuFor(event) {
    const ids = selection();
    if (!ids.length) return;
    event.preventDefault();
    const fixed = (read().collections || []).filter((c) => !c.smart && c.id !== 'last-import');
    const item = (label, run) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => { photoMenu.hidden = true; void run(); });
      return button;
    };
    const rows = [item(`Quick Collection — B`, toss)];
    for (const entry of fixed.filter((c) => c.id !== 'quick')) {
      rows.push(item(`Add to “${entry.name}”`, () => addTo(entry.id, ids)));
    }
    rows.push(item('New collection from selection…', async () => {
      const name = await prompt('New collection from selection', photoMenu);
      if (!name) return;
      const kept = await product.savePhotos(name, ids).catch((error) => { notify(error.message); return null; });
      if (kept) { await refresh(); notify(`“${name}” keeps ${kept.kept} photographs.`); }
    }));
    const here = read().collection;
    const viewing = (read().collections || []).find((c) => c.id === here);
    if (viewing && !viewing.smart) {
      rows.push(item(`Remove from “${viewing.name}”`, () => removeFrom(here, ids)));
    }
    photoMenu.replaceChildren(...rows);
    photoMenu.hidden = false;
    photoMenu.style.left = `${event.clientX}px`;
    photoMenu.style.top = `${event.clientY}px`;
  }

  document.addEventListener('click', (event) => {
    if (!menu.hidden && !menu.contains(event.target)) menu.hidden = true;
    if (!photoMenu.hidden && !photoMenu.contains(event.target)) photoMenu.hidden = true;
    // The click that just opened the popover -- a menu item, Save view, New
    // collection -- bubbles here in the same dispatch; it must not also be
    // the click that closes it.
    const opener = event.target.closest('[data-photo-menu], [data-collection-menu], [data-action]');
    if (!namePop.hidden && !namePop.contains(event.target) && !opener) answer(null);
  });

  return Object.freeze({ refresh, render, show, create, saveView, keepResults, toss, menuFor });
}
