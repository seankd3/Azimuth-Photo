// The search box answers before it is asked. Focusing it offers the
// library's shape — years, cameras, kinds, orientations, albums, and
// what you searched before — and typing narrows those offers while the
// meaning search runs underneath. Every card is a fact the chip language
// can say back, so clicking one applies chips: countable, explainable,
// saveable, and it scopes Rank like any other narrowing. Enter is always
// the meaning search; the cards never take it away. A leading > turns the
// box into the command line: every verb on the screen, by its tooltip.

import { icon } from '../kit/icons.js';
import { said } from './filters.js';

const RECENT_KEY = 'azimuth.recent-searches';

export function createSearchCards({ product, read, _update, box, search, applyChip, commands }) {
  const drop = document.querySelector('[data-search-drop]');
  let facets = null;        // {years, cameras, orientations, roots} once asked
  let sessions = [];        // the latest shoots, named, fetched with the facets
  let asked = 0;            // when facets were fetched; refreshed quietly
  let items = [];           // flat list of actionable rows, top to bottom
  let cursor = -1;

  function recents() {
    try {
      const held = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return Array.isArray(held) ? held.filter((v) => typeof v === 'string') : [];
    } catch {
      return [];
    }
  }

  function remember(text) {
    const held = [text, ...recents().filter((v) => v !== text)].slice(0, 6);
    localStorage.setItem(RECENT_KEY, JSON.stringify(held));
  }

  function forget(text) {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recents().filter((v) => v !== text)));
  }

  async function warm() {
    if (facets && Date.now() - asked < 60_000) return;
    try {
      [facets, sessions] = await Promise.all([
        product.facets(),
        product.sessions().catch(() => sessions),
      ]);
      asked = Date.now();
      // The first focus asks and then answers: the offers appear the moment
      // the shape arrives, as long as the person is still in the box.
      if (document.activeElement === box) render();
    } catch {
      facets = facets || { years: [], cameras: [], orientations: [], roots: [] };
    }
  }

  // ---- what the drop offers, as data ----

  const commanding = () => box.value.trimStart().startsWith('>');

  function offers(text) {
    if (commanding()) {
      const found = commands(text.trimStart().slice(1).trim());
      return found.length ? [['Commands', found]] : [];
    }
    const query = text.trim().toLowerCase();
    const match = (label) => !query || label.toLowerCase().includes(query);
    const held = facets || { years: [], cameras: [], orientations: [], roots: [] };
    const sections = [];

    // What you asked before, first, narrowed by what you type -- a half-typed
    // repeat is exactly what these complete. Each can be forgotten.
    const past = recents().filter(match).slice(0, query ? 3 : 5);
    if (past.length) {
      sections.push(['Recent', past.map((q) => ({
        label: q, glyph: 'recent', run: () => { box.value = q; commit(q); },
        dismiss: () => { forget(q); render(); },
      }))]);
    }
    // One cap for every section: five at rest, three once a word narrows.
    const CAP = query ? 3 : 5;
    const albums = (read().albums || [])
      .filter((c) => (!c.pinned || c.count) && c.count && match(c.name))
      .slice(0, CAP)
      .map((c) => ({
        label: c.name, count: c.count, glyph: c.smart ? 'smart' : 'album',
        run: () => applyChip({ is: 'in', values: [c.id] }),
      }));
    if (albums.length) sections.push(['Albums', albums]);

    // The introduced, with their faces; Someones wait in the sidebar.
    const people = (read().people || []).filter((p) => p.settled && match(p.term)).slice(0, CAP)
      .map((p) => ({
        label: p.term, count: `~${p.count.toLocaleString()}`, glyph: 'person',
        strip: p.samples || [],
        run: () => applyChip({ is: 'person', values: [p.term] }),
      }));
    if (people.length) sections.push(['People', people]);

    // The words you have taught, tilde-counted until calibration.
    const labels = (read().labels || []).filter((l) => match(l.term)).slice(0, CAP)
      .map((l) => ({
        label: l.term, count: `~${l.count.toLocaleString()}`, glyph: 'label',
        run: () => applyChip({ is: 'label', values: [l.term] }),
      }));
    if (labels.length) sections.push(['Labels', labels]);

    // The shoots time itself declares: a session card is a taken chip, so
    // it counts, composes, and saves like every other fact.
    const shoots = (sessions || []).filter((s) => match(s.title)).slice(0, CAP)
      .map((s) => ({
        label: s.title, count: s.count, glyph: 'clock',
        run: () => applyChip({ is: 'taken', from: s.from.slice(0, 10), to: s.to.slice(0, 10) }),
      }));
    if (shoots.length) sections.push(['Sessions', shoots]);

    const years = held.years.filter((y) => match(y.year)).slice(0, CAP)
      .map((y) => ({
        label: y.year, count: y.photos, glyph: 'calendar',
        run: () => applyChip({ is: 'taken', from: `${y.year}-01-01`, to: `${y.year}-12-31` }),
      }));
    if (years.length) sections.push(['Years', years]);

    const cameras = held.cameras.filter((c) => match(c.model)).slice(0, CAP)
      .map((c) => ({
        label: c.model, count: c.photos, glyph: 'camera',
        run: () => applyChip({ is: 'camera', values: [c.model] }),
      }));
    if (cameras.length) sections.push(['Cameras', cameras]);

    const kinds = held.roots.filter((r) => match(r.folder))
      .map((r) => ({
        label: r.folder, count: r.photos, glyph: 'folder',
        run: () => applyChip({ is: 'folder', values: [r.folder] }),
      }));
    const shapes = held.orientations.filter((o) => match(said(o.orientation)))
      .map((o) => ({
        label: said(o.orientation), count: o.photos, glyph: o.orientation === 'portrait' ? 'portrait' : 'landscape',
        run: () => applyChip({ is: 'orientation', values: [o.orientation] }),
      }));
    if (kinds.length || shapes.length) sections.push(['Kind and shape', [...kinds, ...shapes].slice(0, CAP)]);

    return sections;
  }

  // ---- drawing ----

  const hint = document.createElement('kbd');
  hint.textContent = '⏎';
  hint.setAttribute('aria-hidden', 'true');

  function render() {
    const text = box.value;
    const sections = offers(text);
    items = [];
    const rows = [];
    // The row that does what was typed comes first, where the eye and
    // ArrowDown both land; the offers narrow underneath it.
    if (text.trim() && !commanding()) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'drop-row drop-everything';
      row.dataset.item = items.length;
      row.textContent = `Search everything for “${text.trim()}”`;
      row.append(hint);
      rows.push(row);
      items.push({ run: () => commit(text) });
    }
    if (commanding() && !sections.length) {
      const none = document.createElement('p');
      none.className = 'eyebrow';
      none.textContent = 'No verb on the screen matches';
      none.setAttribute('role', 'presentation');
      rows.push(none);
    }
    for (const [title, entries] of sections) {
      const head = document.createElement('p');
      head.className = 'eyebrow';
      head.textContent = title;
      head.setAttribute('role', 'presentation');
      rows.push(head);
      for (const entry of entries) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'drop-row';
        row.dataset.item = items.length;
        const glyph = document.createElement('span');
        glyph.className = 'drop-glyph';
        glyph.append(icon(entry.glyph));
        const label = document.createElement('span');
        label.className = 'drop-label';
        label.textContent = entry.label;
        row.append(glyph, label);
        if (entry.strip?.length) {
          // The faces of the group: best-ranked tiles, or for a person the
          // face itself, cropped by the box the worker found it in.
          const strip = document.createElement('span');
          strip.className = 'drop-strip';
          for (const sample of entry.strip.slice(0, 3)) {
            const tile = document.createElement('img');
            tile.src = sample.tile;
            tile.alt = '';
            tile.decoding = 'async';
            if (sample.view) {
              tile.style.objectViewBox = sample.view;
              tile.classList.add('face');
            }
            strip.append(tile);
          }
          row.append(strip);
        }
        if (entry.count !== undefined) {
          const count = document.createElement('span');
          count.className = 'drop-count';
          count.textContent = typeof entry.count === 'number' ? entry.count.toLocaleString() : entry.count;
          row.append(count);
        }
        if (entry.dismiss) {
          // A button beside a button, never inside one: the row is wrapped.
          const dismiss = document.createElement('button');
          dismiss.type = 'button';
          dismiss.className = 'drop-dismiss';
          dismiss.textContent = '×';
          dismiss.setAttribute('aria-label', 'Forget this search');
          dismiss.addEventListener('click', (event) => { event.stopPropagation(); entry.dismiss(); });
          const pair = document.createElement('span');
          pair.className = 'drop-pair';
          pair.setAttribute('role', 'presentation');
          row.title = 'Delete forgets this search';
          pair.append(row, dismiss);
          rows.push(pair);
        } else rows.push(row);
        items.push(entry);
      }
    }
    for (const row of rows) {
      const option = row.matches('.drop-row') ? row : row.querySelector('.drop-row');
      if (option) { option.id = `search-offer-${option.dataset.item}`; option.setAttribute('role', 'option'); }
    }
    cursor = -1;
    movedSince = false;
    drop.replaceChildren(...rows);
    drop.hidden = rows.length === 0;
    box.setAttribute('aria-expanded', String(!drop.hidden));
    // Enter's row is the active descendant from the first keystroke.
    mark();
    place();
  }

  function place() {
    const at = box.getBoundingClientRect();
    drop.style.left = `${at.left}px`;
    drop.style.top = `${at.bottom + 6}px`;
    drop.style.width = `${at.width}px`;
  }

  function mark() {
    for (const row of drop.querySelectorAll('.drop-row')) {
      row.classList.toggle('is-cursor', Number(row.dataset.item) === cursor);
    }
    // The ⏎ rides whatever Enter would run: the cursor row, else the
    // everything row when there is one, else the first command.
    const current = drop.querySelector('.is-cursor') || drop.querySelector('.drop-everything')
      || (commanding() ? drop.querySelector('.drop-row') : null);
    if (current) current.append(hint);
    if (current) box.setAttribute('aria-activedescendant', current.id);
    else box.removeAttribute('aria-activedescendant');
    current?.scrollIntoView({ block: 'nearest' });
  }

  function close() {
    drop.hidden = true;
    items = [];
    cursor = -1;
    box.setAttribute('aria-expanded', 'false');
    box.removeAttribute('aria-activedescendant');
  }

  function commit(text) {
    const query = text.trim();
    if (query) remember(query);
    search(query);
  }

  function pick(index) {
    const entry = items[index];
    if (!entry) return;
    close();
    entry.run();
  }

  // ---- the box, owned here ----

  let typeTimer = null;
  // Where the pointer is, and where it was when the keyboard last moved the
  // cursor: a row scrolled under a still mouse must not take the cursor.
  let pointerAt = null;
  let keyedAt = null;
  // A row that appears under a still mouse must not take the cursor: only
  // a pointer that has moved since the last render may.
  let movedSince = false;
  box.setAttribute('role', 'combobox');
  box.setAttribute('aria-autocomplete', 'list');
  box.setAttribute('aria-expanded', 'false');
  box.setAttribute('aria-controls', 'search-drop');
  drop.id = 'search-drop';
  drop.setAttribute('role', 'listbox');

  box.addEventListener('focus', () => { void warm(); render(); });
  box.addEventListener('input', () => {
    render();
    // The meaning search runs underneath as it always has.
    clearTimeout(typeTimer);
    // Typing searches; only Enter or a card remembers the words. A
    // command line is not a query.
    if (!commanding()) typeTimer = setTimeout(() => search(box.value.trim()), 300);
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (drop.hidden) render();
      if (!items.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      cursor = (cursor + step + items.length + 1) % (items.length + 1);
      if (cursor === items.length) cursor = -1;   // back to the box itself
      keyedAt = pointerAt;
      mark();
      event.preventDefault();
      return;
    }
    if (event.key === 'Enter') {
      clearTimeout(typeTimer);
      if (cursor >= 0) pick(cursor);
      else if (commanding()) { if (items.length) pick(0); else close(); }
      else { close(); commit(box.value); }
      event.preventDefault();
      return;
    }
    if ((event.key === 'Delete' || event.key === 'Backspace') && cursor >= 0 && items[cursor]?.dismiss && !box.value) {
      // On a Recent row, Delete forgets it and keeps the cursor's place.
      const at = cursor;
      items[cursor].dismiss();
      cursor = Math.min(at, items.length - 1);
      mark();
      event.preventDefault();
      return;
    }
    if (event.key === 'Escape') {
      clearTimeout(typeTimer);
      // First Esc puts the offers away; the second clears the words and
      // keeps the focus, so a new search can start at once. An empty box
      // with nothing to put away lets Esc through to the app's own ladder,
      // whose last rung hands the keyboard back to the photographs.
      if (!drop.hidden) close();
      else if (box.value) { box.value = ''; search(''); }
      else return;
      event.stopPropagation();
      event.preventDefault();
    }
  });

  drop.addEventListener('pointerdown', (event) => {
    // The click must not blur the box before it lands.
    event.preventDefault();
  });
  drop.addEventListener('click', (event) => {
    const row = event.target.closest('.drop-row');
    if (row) pick(Number(row.dataset.item));
  });
  drop.addEventListener('pointermove', (event) => { pointerAt = [event.clientX, event.clientY]; movedSince = true; });
  drop.addEventListener('pointerover', (event) => {
    const row = event.target.closest('.drop-row');
    if (!row || !movedSince) return;
    if (keyedAt && pointerAt && keyedAt[0] === pointerAt[0] && keyedAt[1] === pointerAt[1]) return;
    cursor = Number(row.dataset.item);
    mark();
  });
  document.addEventListener('click', (event) => {
    if (!drop.hidden && !drop.contains(event.target) && event.target !== box) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !drop.hidden && document.activeElement !== box) {
      close();
      event.stopImmediatePropagation();
      event.preventDefault();
    }
  });
  window.addEventListener('resize', () => { if (!drop.hidden) place(); });

  return Object.freeze({ close, isOpen: () => !drop.hidden });
}
