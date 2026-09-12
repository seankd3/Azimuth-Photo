// The filter chips: the same criteria language smart albums store,
// worn in the contextbar. A chip is one sentence — stars at least three, in
// either of these albums, not the RP — chips AND across, values OR
// within, and any chip excludes instead. A filter is an unsaved smart
// album; saving it is what names it.
//
// The editor edits a chip of its own; the bar only ever holds chips the
// library can execute. A new chip joins on its first real value, an emptied
// chip leaves, and nothing half-made can reach a query.

import { showMenu, hideMenu } from '../kit/menu.js';
import { icon } from '../kit/icons.js';

// The one vocabulary: what a field is called wherever it appears (the
// menu, the editor's title), and what a stored value is called on a chip.
const FIELD_LABEL = { stars: 'Stars', taken: 'Date taken', camera: 'Camera', status: 'Pick', orientation: 'Orientation', look: 'Look', person: 'Person', label: 'Label', alike: 'Alike', in: 'In album', folder: 'Folder', stack: 'Stack' };
const VALUE_LABEL = { picked: 'Picked', unflagged: 'Unflagged', landscape: 'Wide (landscape)', portrait: 'Tall (portrait)', square: 'Square', color: 'Color', bw: 'Black & white', sepia: 'Sepia' };
// The fields the + menu offers, and the ones an editor can change; a
// folder or stack chip is lifted from the view and only ever removed.
const OFFERED = ['stars', 'taken', 'camera', 'status', 'orientation', 'look', 'person', 'label', 'in'];
// Exported so the search drop offers a shape in the words its chip will wear.
// "Landscape" alone reads as a subject (the owner, 09-12: "Landscape photos vs
// landscape orientation"); the shape word comes first, the camera word after.
export const said = (value) => VALUE_LABEL[value] || value;

export function createFilterBar({ product, read, update, onChange, undo = null }) {
  const bar = document.querySelector('[data-chips]');
  const menu = document.querySelector('[data-chip-menu]');
  const editor = document.querySelector('[data-chip-editor]');
  let editing = null;   // { chip, index } while the editor is open; index null until the chip is in the bar
  let changeTimer = null;

  function chips() {
    return read().chips || [];
  }

  function say(chip, state) {
    const not = chip.not ? 'not ' : '';
    if (chip.is === 'stars') return `${not}★ ${chip.least}+`;
    if (chip.is === 'taken') {
      const from = chip.from || '…';
      const to = chip.to || 'now';
      return `${not}${from} – ${to}`;
    }
    if (chip.is === 'in') {
      const names = new Map((state.albums || []).map((c) => [c.id, c.name]));
      return `${not}in ${chip.values.map((v) => names.get(v) || v).join(' or ')}`;
    }
    if (chip.is === 'alike') return `${not}≈ ${chip.values.join(' or ')}`;
    if (chip.is === 'stack') return `${not}In this stack`;
    return `${not}${chip.values.map(said).join(' or ')}`;
  }

  function describe(state) {
    // The view as one sentence — what Save view proposes to call it.
    return chips().map((chip) => say(chip, state)).join(' · ');
  }

  let shown = '';
  function render(state) {
    // The bar is rebuilt only when it would read differently: every store
    // update passes through here, and a rebuild mid-click drops the click.
    const key = chips().map((chip) => `${chip.not ? '!' : ''}${say(chip, state)}`).join('\u0001');
    if (key === shown) return;
    shown = key;
    bar.replaceChildren(...chips().map((chip, index) => {
      // Two real buttons in one pill: the words open the editor, × removes.
      const pill = document.createElement('span');
      pill.className = 'chip' + (chip.not ? ' is-not' : '');
      const label = document.createElement('button');
      label.type = 'button';
      label.className = 'chip-label';
      label.dataset.chip = index;
      label.textContent = say(chip, state);
      label.title = OFFERED.includes(chip.is) ? `${FIELD_LABEL[chip.is]} — click to change, Backspace removes` : 'Backspace removes';
      const drop = document.createElement('button');
      drop.type = 'button';
      drop.className = 'drop-chip';
      drop.dataset.drop = index;
      drop.append(icon('close'));
      drop.setAttribute('aria-label', 'Remove this filter');
      pill.append(label, drop);
      return pill;
    }));
  }

  function commit(next) {
    // The pills answer instantly; the library follows one beat later, so a
    // run of checkbox ticks costs one reload, not one per tick.
    update({ chips: next });
    render(read());
    clearTimeout(changeTimer);
    changeTimer = setTimeout(onChange, 200);
  }

  function place(box, anchor) {
    const at = anchor.getBoundingClientRect();
    box.hidden = false;
    box.style.left = `${Math.max(12, Math.min(at.left, window.innerWidth - box.offsetWidth - 12))}px`;
    const below = at.bottom + 6;
    const fits = below + box.offsetHeight + 12 <= window.innerHeight;
    box.style.top = `${fits ? below : Math.max(12, at.top - box.offsetHeight - 6)}px`;
  }

  function closeEditor() {
    editor.hidden = true;
    editor.replaceChildren();
    editing = null;
  }

  // ---- the one editor, drawn for the chip's field ----

  function checkRow(label, value, checked) {
    const row = document.createElement('label');
    row.className = 'check-row';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = checked;
    box.dataset.value = value;
    row.append(box, document.createTextNode(label));
    return row;
  }

  async function openEditor(chip, index, anchor) {
    editing = { chip, index };
    editor.replaceChildren();
    const title = document.createElement('p');
    title.className = 'eyebrow';
    title.id = 'chip-editor-title';
    title.textContent = FIELD_LABEL[chip.is];
    editor.append(title);

    if (chip.is === 'stars') {
      const row = document.createElement('div');
      row.className = 'star-row';
      for (let least = 1; least <= 5; least += 1) {
        const star = document.createElement('button');
        star.type = 'button';
        star.textContent = `${least}+`;
        star.classList.toggle('is-on', chip.least === least);
        star.addEventListener('click', () => { chip.least = least; save(); });
        row.append(star);
      }
      editor.append(row);
    } else if (chip.is === 'taken') {
      for (const edge of ['from', 'to']) {
        const row = document.createElement('label');
        row.className = 'row';
        const date = document.createElement('input');
        date.type = 'date';
        date.value = chip[edge] || '';
        date.addEventListener('change', () => { chip[edge] = date.value; save(false); });
        row.append(document.createTextNode(edge === 'from' ? 'From' : 'To'), date);
        editor.append(row);
      }
    } else {
      let offered = [];
      if (chip.is === 'camera') {
        offered = (await product.cameras()).map((c) => [c.model, `${c.model} · ${c.photos.toLocaleString()}`]);
      } else if (chip.is === 'status') {
        offered = ['picked', 'unflagged'].map((v) => [v, said(v)]);
      } else if (chip.is === 'orientation') {
        offered = ['landscape', 'portrait', 'square'].map((v) => [v, said(v)]);
      } else if (chip.is === 'look') {
        offered = ['color', 'bw', 'sepia'].map((v) => [v, said(v)]);
      } else if (chip.is === 'person') {
        const known = (read().people || []).filter((p) => p.settled).map((p) => p.term);
        offered = [...new Set([...chip.values, ...known])].map((t) => [t, t]);
      } else if (chip.is === 'label') {
        const taught = (read().labels || []).map((l) => l.term);
        offered = [...new Set([...chip.values, ...taught])].map((t) => [t, t]);
      } else {
        // The album being looked at would only re-say the view.
        offered = (read().albums || [])
          .filter((c) => (!c.pinned || c.count) && c.id !== read().album)
          .map((c) => [c.id, c.name]);
      }
      for (const [value, label] of offered) {
        const row = checkRow(label, value, chip.values.includes(value));
        row.querySelector('input').addEventListener('change', (event) => {
          const held = new Set(chip.values);
          if (event.target.checked) held.add(value);
          else held.delete(value);
          chip.values = [...held];
          save(false);
        });
        editor.append(row);
      }
    }

    const invert = document.createElement('label');
    invert.className = 'check-row not-row';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = Boolean(chip.not);
    box.addEventListener('change', () => { chip.not = box.checked; save(false); });
    invert.append(box, document.createTextNode('Exclude these instead'));
    editor.append(invert);

    function save(close = true) {
      if (!editing) return;
      const next = chips().slice();
      if ('values' in chip && chip.values.length === 0) {
        // Unchecking the last value is the person saying "done with this".
        if (editing.index !== null) {
          next.splice(editing.index, 1);
          commit(next);
        }
        closeEditor();
        return;
      }
      if (editing.index === null) {
        editing.index = next.length;
        next.push(chip);
      } else {
        next[editing.index] = { ...chip };
      }
      commit(next);
      if (close) closeEditor();
    }

    place(editor, anchor);
    editor.querySelector('input, button')?.focus();
    // Stars and dates are born valid — the default is already a sentence, so
    // it applies on open and the editor refines it. A values chip waits for
    // its first value.
    if (editing.index === null && !('values' in chip)) save(false);
  }

  // ---- wiring ----

  // The + menu is the vocabulary, spoken once.
  menu.replaceChildren(...OFFERED.map((field) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.setAttribute('role', 'menuitem');
    item.dataset.field = field;
    item.textContent = FIELD_LABEL[field];
    return item;
  }));

  function dropChip(dropped) {
    const held = chips().slice();
    const next = held.slice();
    const [gone] = next.splice(dropped, 1);
    if (editing && editing.index !== null) {
      if (editing.index === dropped) closeEditor();
      else if (editing.index > dropped) editing.index -= 1;
    }
    commit(next);
    // A filter taken off is a change to what is being looked at; it has
    // the same way back as any other.
    undo?.show(`Filter removed — ${say(gone, read())}.`, () => commit(held));
  }

  bar.addEventListener('keydown', (event) => {
    if (event.key !== 'Backspace' && event.key !== 'Delete') return;
    const held = event.target.closest('[data-chip], [data-drop]');
    if (!held) return;
    const at = Number(held.dataset.chip ?? held.dataset.drop);
    dropChip(at);
    const labels = bar.querySelectorAll('[data-chip]');
    (labels[Math.min(at, labels.length - 1)] || document.querySelector('[data-action="add-chip"]'))?.focus();
    event.preventDefault();
  });

  document.querySelector('[data-action="add-chip"]').addEventListener('click', (event) => {
    const at = event.currentTarget.getBoundingClientRect();
    showMenu(menu, at.left, at.bottom + 6);
    event.stopPropagation();
  });

  menu.addEventListener('click', (event) => {
    const field = event.target.closest('[data-field]')?.dataset.field;
    if (!field) return;
    hideMenu(menu);
    const fresh = field === 'stars' ? { is: 'stars', least: 3 }
      : field === 'taken' ? { is: 'taken', from: '', to: '' }
      : { is: field, values: [] };
    void openEditor(fresh, null, document.querySelector('[data-action="add-chip"]'));
  });

  bar.addEventListener('click', (event) => {
    const drop = event.target.closest('[data-drop]');
    if (drop) { dropChip(Number(drop.dataset.drop)); return; }
    const pill = event.target.closest('[data-chip]');
    if (pill) {
      const index = Number(pill.dataset.chip);
      // A folder or stack chip has no editor; it is the view, lifted.
      if (OFFERED.includes(chips()[index]?.is)) void openEditor({ ...chips()[index] }, index, pill);
    }
  });

  document.addEventListener('click', (event) => {
    if (!editor.hidden && !editor.contains(event.target)
        && !event.target.closest('[data-chip]') && !event.target.closest('[data-field]')) {
      closeEditor();
    }
    if (!menu.hidden && !menu.contains(event.target)) hideMenu(menu);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || (editor.hidden && menu.hidden)) return;
    const at = editing?.index ?? null;
    closeEditor();
    hideMenu(menu);
    // The keyboard goes back to the chip it came from, or to the + button.
    ((at !== null && bar.querySelector(`[data-chip="${at}"]`)) || document.querySelector('[data-action="add-chip"]'))?.focus();
    event.preventDefault();
    event.stopImmediatePropagation();
  });

  return Object.freeze({ render, describe, close: closeEditor, word: (chip) => say(chip, read()) });
}
