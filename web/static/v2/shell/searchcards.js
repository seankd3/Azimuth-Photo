// The search box answers before it is asked. Focusing it offers the
// library's shape — years, cameras, kinds, orientations, albums, and
// what you searched before — and typing narrows those offers while the
// meaning search runs underneath. Every card is a fact the chip language
// can say back, so clicking one applies chips: countable, explainable,
// saveable, and it scopes Rank like any other narrowing. Enter is always
// the meaning search; the cards never take it away.

const RECENT_KEY = 'azimuth.recent-searches';

export function createSearchCards({ product, read, update, box, search, applyChip }) {
  const drop = document.querySelector('[data-search-drop]');
  let facets = null;        // {years, cameras, orientations, roots} once asked
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

  async function warm() {
    if (facets && Date.now() - asked < 60_000) return;
    try {
      facets = await product.facets();
      asked = Date.now();
      // The first focus asks and then answers: the offers appear the moment
      // the shape arrives, as long as the person is still in the box.
      if (document.activeElement === box) render();
    } catch {
      facets = facets || { years: [], cameras: [], orientations: [], roots: [] };
    }
  }

  // ---- what the drop offers, as data ----

  const SAY = { landscape: 'Landscape', portrait: 'Portrait', square: 'Square' };

  function offers(text) {
    const query = text.trim().toLowerCase();
    const match = (label) => !query || label.toLowerCase().includes(query);
    const held = facets || { years: [], cameras: [], orientations: [], roots: [] };
    const sections = [];

    if (!query) {
      const past = recents();
      if (past.length) {
        sections.push(['Recent', past.map((q) => ({
          label: q, glyph: '↻', run: () => { box.value = q; search(q); },
        }))]);
      }
    }
    const albums = (read().albums || [])
      .filter((c) => (!c.pinned || c.count) && c.count && match(c.name))
      .slice(0, 6)
      .map((c) => ({
        label: c.name, count: c.count, glyph: c.smart ? '◇' : '▣',
        run: () => applyChip({ is: 'in', values: [c.id] }),
      }));
    if (albums.length) sections.push(['Albums', albums]);

    // The introduced, with their faces; Someones wait in the sidebar.
    const people = (read().people || []).filter((p) => p.settled && match(p.term)).slice(0, query ? 3 : 4)
      .map((p) => ({
        label: p.term, count: `~${p.count.toLocaleString()}`, glyph: '◉',
        strip: p.samples || [],
        run: () => applyChip({ is: 'person', values: [p.term] }),
      }));
    if (people.length) sections.push(['People', people]);

    // The words you have taught, tilde-counted until calibration.
    const labels = (read().labels || []).filter((l) => match(l.term)).slice(0, query ? 4 : 5)
      .map((l) => ({
        label: l.term, count: `~${l.count.toLocaleString()}`, glyph: '◇',
        run: () => applyChip({ is: 'label', values: [l.term] }),
      }));
    if (labels.length) sections.push(['Labels', labels]);

    const years = held.years.filter((y) => match(y.year)).slice(0, query ? 3 : 6)
      .map((y) => ({
        label: y.year, count: y.photos, glyph: '▤',
        run: () => applyChip({ is: 'taken', from: `${y.year}-01-01`, to: `${y.year}-12-31` }),
      }));
    if (years.length) sections.push(['Years', years]);

    const cameras = held.cameras.filter((c) => match(c.model)).slice(0, query ? 4 : 5)
      .map((c) => ({
        label: c.model, count: c.photos, glyph: '⊙',
        run: () => applyChip({ is: 'camera', values: [c.model] }),
      }));
    if (cameras.length) sections.push(['Cameras', cameras]);

    const kinds = held.roots.filter((r) => match(r.folder))
      .map((r) => ({
        label: r.folder, count: r.photos, glyph: '▸',
        run: () => applyChip({ is: 'folder', values: [r.folder] }),
      }));
    const shapes = held.orientations.filter((o) => match(SAY[o.orientation] || ''))
      .map((o) => ({
        label: SAY[o.orientation], count: o.photos, glyph: o.orientation === 'portrait' ? '▯' : '▭',
        run: () => applyChip({ is: 'orientation', values: [o.orientation] }),
      }));
    if (kinds.length || shapes.length) sections.push(['Kind and shape', [...kinds, ...shapes]]);

    return sections;
  }

  // ---- drawing ----

  function render() {
    const text = box.value;
    const sections = offers(text);
    items = [];
    const rows = [];
    for (const [title, entries] of sections) {
      const head = document.createElement('p');
      head.className = 'eyebrow';
      head.textContent = title;
      rows.push(head);
      for (const entry of entries) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'drop-row';
        row.dataset.item = items.length;
        const glyph = document.createElement('span');
        glyph.className = 'drop-glyph';
        glyph.textContent = entry.glyph;
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
        rows.push(row);
        items.push(entry);
      }
    }
    if (text.trim()) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'drop-row drop-everything';
      row.dataset.item = items.length;
      row.textContent = `Search everything for “${text.trim()}”`;
      const hint = document.createElement('kbd');
      hint.textContent = '⏎';
      row.append(hint);
      rows.push(row);
      items.push({ run: () => commit(text) });
    }
    cursor = -1;
    drop.replaceChildren(...rows);
    drop.hidden = rows.length === 0;
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
    drop.querySelector('.is-cursor')?.scrollIntoView({ block: 'nearest' });
  }

  function close() {
    drop.hidden = true;
    items = [];
    cursor = -1;
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

  box.addEventListener('focus', () => { void warm(); render(); });
  box.addEventListener('input', () => {
    render();
    // The meaning search runs underneath as it always has.
    clearTimeout(typeTimer);
    typeTimer = setTimeout(() => commit(box.value), 300);
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (drop.hidden) render();
      if (!items.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      cursor = (cursor + step + items.length + 1) % (items.length + 1);
      if (cursor === items.length) cursor = -1;   // back to the box itself
      mark();
      event.preventDefault();
      return;
    }
    if (event.key === 'Enter') {
      clearTimeout(typeTimer);
      if (cursor >= 0) pick(cursor);
      else { close(); commit(box.value); }
      event.preventDefault();
      return;
    }
    if (event.key === 'Escape') {
      clearTimeout(typeTimer);
      if (!drop.hidden) {
        // First Esc puts the offers away; the second clears as it always did.
        close();
      } else {
        box.value = '';
        search('');
        box.blur();
      }
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
  drop.addEventListener('pointerover', (event) => {
    const row = event.target.closest('.drop-row');
    if (!row) return;
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
