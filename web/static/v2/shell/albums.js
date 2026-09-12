// Albums in the sidebar and the verbs around them. One primitive worn
// two ways: plain albums hold what you put in them (B, right-click,
// drag); smart ones hold whatever their chips match, and Freeze turns the
// second into the first — the album you are about to share elsewhere. The
// shelf is the name: America/Utah sits under America, and a parent browses
// as the union of what is under it.

import { acceptDrops } from '../kit/drop.js';
import { why } from '../kit/why.js';
import { showMenu, hideMenu } from '../kit/menu.js';
import { icon } from '../kit/icons.js';
import { count } from '../kit/words.js';

export function createAlbumsPanel({ product, read, update, notify, undo, reload, moved, selection, describe, viewOf }) {
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
      notify(why(error));
    }
  }

  function show(id) {
    // The view moves; whichever stage is up follows it. Rank re-scopes in
    // place, the grid reloads. One rule for every place -- a folder, an
    // album, All photos: choosing it ends the search and keeps the chips.
    document.querySelector('[data-search]').value = '';
    if (read().view === 'rank') update({ album: id, folders: [], query: '', like: [] });
    else update({ view: 'library', album: id, folders: [], query: '', like: [], selected: null, selectedIndex: null });
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
      row.prepend(icon(entry.smart ? 'smart' : 'album'));
      if (entry.smart) row.title = `${entry.name} — smart: fills itself from its filters`;
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
  let openedBy = null;    // the click that opened it bubbles to document once
  let taken = () => [];   // names the answer may not repeat
  let joining = () => []; // names the answer may share: a person healing
  let noun = 'album';     // what a taken name is called
  const nameError = namePop.querySelector('[data-name-error]');

  function prompt(title, anchor, initial = '', { not = [], kind = 'album', joins = [] } = {}) {
    // The anchor is a node or a rect captured before its menu was hidden —
    // a hidden node measures at the corner of the window.
    askName?.(null);
    return new Promise((resolve) => {
      askName = resolve;
      namedFrom = anchor instanceof Element ? anchor : null;
      openedBy = window.event || null;
      taken = () => not.map((n) => n.toLowerCase());
      joining = () => joins;
      noun = kind;
      nameError.textContent = '';
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

  // A name already on the shelf is refused where it is typed, with the
  // text kept, instead of after the popover has closed.
  function checkName() {
    const value = nameInput.value.trim();
    const dup = value && taken().includes(value.toLowerCase());
    // A name someone already answers to is not a clash for a person: it
    // is how a split heals. The field says so before Save is pressed.
    // The join keeps the spelling already in use: "alice" joins "Alice".
    const join = !dup && value ? joined(value) : null;
    nameError.classList.toggle('is-note', Boolean(join));
    nameError.setAttribute('role', join ? 'status' : 'alert');
    nameError.textContent = dup ? `There is already ${noun === 'album' ? 'an album' : `a ${noun}`} called “${value}”.`
      : join ? `Joins “${join}” — the two become one person.` : '';
    nameOk.disabled = !value || dup;
    return Boolean(value) && !dup;
  }
  const joined = (value) => joining().find((n) => n.toLowerCase() === value.toLowerCase()) || null;
  const said = () => { const value = nameInput.value.trim(); return joined(value) || value; };
  nameInput.addEventListener('input', checkName);
  nameOk.addEventListener('click', () => { if (checkName()) answer(said()); });
  namePop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target === nameInput) {
      // An empty or taken name is not an answer; the field simply waits.
      if (checkName()) answer(said());
      event.preventDefault();
    }
    if (event.key === 'Escape') { answer(null); event.preventDefault(); event.stopPropagation(); }
    if (event.key === 'Tab') {
      // Two stops, field and Save, and nothing past them.
      const forward = !event.shiftKey;
      const onField = event.target === nameInput;
      if ((forward && !onField) || (!forward && onField)) { answer(null); return; }
      (onField ? nameOk : nameInput).focus();
      event.preventDefault();
    }
  });

  // ---- verbs ----

  const albumNames = () => (read().albums || []).map((c) => c.name);

  async function create(anchor) {
    const name = await prompt('New album', anchor, '', { not: albumNames() });
    if (!name) return;
    try {
      await product.createAlbum(name);
      await refresh();
    } catch (error) {
      notify(why(error));
    }
  }

  async function saveView(anchor) {
    // The chips already say what this view is; the name box opens saying it.
    const name = await prompt('Save this view as', anchor, describe(read()), { not: albumNames() });
    if (!name) return;
    try {
      await product.saveView(name, viewOf());
      await refresh();
      notify(`“${name}” keeps these filters, live.`);
    } catch (error) {
      notify(why(error));
    }
  }

  async function keepResults(anchor) {
    const name = await prompt('Save these results as', anchor, read().query, { not: albumNames() });
    if (!name) return;
    try {
      const answered = await product.find({
        query: read().query, like: read().like || [], limit: 1000, offset: 0, view: viewOf() });
      const ids = answered.photos.map((p) => p.id);
      const kept = await product.savePhotos(name, ids);
      await refresh();
      notify(answered.total > kept.kept
        ? `“${name}” keeps the top ${kept.kept.toLocaleString()} of ${answered.total.toLocaleString()} results.`
        : `“${name}” keeps ${count(kept.kept, 'photograph')} from this search.`);
    } catch (error) {
      notify(why(error));
    }
  }

  async function quickToss(ids) {
    try {
      const moved = await product.quick(ids);
      await refresh();
      if (read().album === 'quick') await reload();
      const word = (n) => count(n, 'photograph');
      notify('added' in moved
        ? `${word(moved.added)} added to Quick album — ${moved.count.toLocaleString()} there now.`
        : `${word(moved.removed)} out of Quick album — ${moved.count.toLocaleString()} there now.`);
    } catch (error) {
      notify(why(error));
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
      const word = count(out.removed, 'photograph');
      notify(here.smart
        ? `${word} excluded from “${here.name}”.`
        : `${word} out of “${here.name}”.`);
    } catch (error) {
      notify(why(error));
    }
  }

  async function addTo(id, ids) {
    try {
      const added = await product.addToAlbum(id, ids);
      await refresh();
      const name = (read().albums.find((c) => c.id === id) || {}).name || 'the album';
      // A drop into the wrong album has the same way back as every other
      // change to a set.
      undo.show(`${count(added.added, 'photograph')} added to “${name}”.`, async () => {
        await product.removeFromAlbum(id, ids);
        await refresh();
        if (read().album === id) await reload();
      });
    } catch (error) {
      notify(why(error));
    }
  }

  async function removeFrom(id, ids) {
    try {
      await product.removeFromAlbum(id, ids);
      await refresh();
      await reload();
    } catch (error) {
      notify(why(error));
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
    showMenu(menu, event.clientX, event.clientY);
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
        const name = await prompt('Rename to', row || tree, current, { not: albumNames().filter((n) => n !== current) });
        if (name) {
          const renamed = await product.renameAlbum(id, name);
          notify(`“${current}” renamed to “${name}”.${renamed?.followed ? ` ${count(renamed.followed, 'album')} under it followed.` : ''}`);
        }
      }
      if (action === 'freeze-album') {
        const frozen = await product.freezeAlbum(id);
        undo.show(`Frozen — ${frozen.frozen.toLocaleString()} photographs are now yours to edit by hand.`, async () => {
          await product.redefineAlbum(id, frozen.criteria);
          await refresh();
          if (read().album === id) await reload();
        });
      }
      if (action === 'delete-album') {
        const name = (read().albums.find((c) => c.id === id) || {}).name || 'Album';
        await product.forgetAlbum(id);
        if (read().album === id) {
          update({ album: null });
          await reload();
        }
        undo.show(`“${name.split('/').pop()}” deleted.`, async () => {
          await product.rememberAlbum(id);
          await refresh();
        });
      }
      await refresh();
    } catch (error) {
      notify(why(error));
    }
  });

  // Smart albums take the drop too: what you drag in is pinned in past the
  // rules -- the exception the pro tools never had. What came in last is a
  // fact, not a shelf.
  acceptDrops(tree, '[data-album]', {
    judge: (row) => (row.dataset.album === 'last-import' ? 'Last import is a record, not a shelf' : null),
    drop: (row, ids) => addTo(row.dataset.album, ids),
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
      button.setAttribute('role', 'menuitem');
      button.textContent = label;
      button.addEventListener('click', () => { hideMenu(photoMenu); void run(); });
      return button;
    };
    const here = read().album;
    const viewing = (read().albums || []).find((c) => c.id === here);
    // B belongs to whichever verb it drives here: the prune inside an
    // album, the Quick toss everywhere else.
    const pruning = viewing && here !== 'quick' && here !== 'last-import';
    const rows = verbs.map(({ label, run }) => item(label, run));
    // Trash is not a place to file from: its menu is its own verbs.
    if (read().view === 'trash') {
      photoMenu.replaceChildren(...rows);
      showMenu(photoMenu, event.clientX, event.clientY);
      return;
    }
    rows.push(item(pruning ? 'Quick album' : 'Quick album — B', () => quickToss(ids)));
    for (const entry of fixed.filter((c) => c.id !== 'quick')) {
      rows.push(item(`Add to “${entry.name}”`, () => addTo(entry.id, ids)));
    }
    rows.push(item('New album from selection…', async () => {
      const name = await prompt('New album from selection', spot, '', { not: albumNames() });
      if (!name) return;
      const kept = await product.savePhotos(name, ids).catch((error) => { notify(why(error)); return null; });
      if (kept) { await refresh(); notify(`“${name}” keeps ${kept.kept.toLocaleString()} photographs.`); }
    }));
    if (viewing) {
      rows.push(item(
        (viewing.smart ? `Exclude from “${viewing.name}”` : `Remove from “${viewing.name}”`)
          + (pruning ? ' — B' : ''),
        () => removeFrom(here, ids)));
    }
    photoMenu.replaceChildren(...rows);
    showMenu(photoMenu, event.clientX, event.clientY);
  }

  document.addEventListener('click', (event) => {
    if (!menu.hidden && !menu.contains(event.target)) hideMenu(menu);
    if (!photoMenu.hidden && !photoMenu.contains(event.target)) hideMenu(photoMenu);
    // The click that just opened the popover bubbles here in the same
    // dispatch; it must not also be the click that closes it. Every later
    // outside click answers "no", whatever else it goes on to do.
    if (event === openedBy) return;
    if (!namePop.hidden && !namePop.contains(event.target)) answer(null);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || (menu.hidden && photoMenu.hidden && namePop.hidden)) return;
    hideMenu(menu);
    hideMenu(photoMenu);
    if (!namePop.hidden) answer(null);
    event.preventDefault();
    event.stopImmediatePropagation();
  });

  return Object.freeze({ refresh, render, show, create, saveView, keepResults, toss, menuFor, ask: prompt });
}
