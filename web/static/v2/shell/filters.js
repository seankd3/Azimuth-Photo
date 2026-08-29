// The filter chips: the same criteria language smart albums store,
// worn in the contextbar. A chip is one sentence — stars at least three, in
// either of these albums, not the RP — chips AND across, values OR
// within, and any chip excludes instead. A filter is an unsaved smart
// album; saving it is what names it.
//
// The editor edits a chip of its own; the bar only ever holds chips the
// library can execute. A new chip joins on its first real value, an emptied
// chip leaves, and nothing half-made can reach a query.

const FIELD_LABEL = { stars: 'Stars', taken: 'Taken', camera: 'Camera', status: 'Pick', orientation: 'Orientation', look: 'Look', person: 'Person', label: 'Label', alike: 'Alike', in: 'In' };

export function createFilterBar({ product, read, update, onChange }) {
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
    if (chip.is === 'stack') return `${not}▤ In this stack`;
    return `${not}${chip.values.join(' or ')}`;
  }

  function describe(state) {
    // The view as one sentence — what Save view proposes to call it.
    return chips().map((chip) => say(chip, state)).join(' · ');
  }

  function render(state) {
    bar.replaceChildren(...chips().map((chip, index) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'chip' + (chip.not ? ' is-not' : '');
      pill.dataset.chip = index;
      const label = document.createElement('span');
      label.textContent = say(chip, state);
      const drop = document.createElement('span');
      drop.className = 'drop-chip';
      drop.dataset.drop = index;
      drop.textContent = '×';
      drop.setAttribute('role', 'button');
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
        offered = [['picked', 'Picked'], ['unflagged', 'Unflagged']];
      } else if (chip.is === 'orientation') {
        offered = [['landscape', 'Landscape'], ['portrait', 'Portrait'], ['square', 'Square']];
      } else if (chip.is === 'look') {
        offered = [['color', 'Color'], ['bw', 'Black & white'], ['sepia', 'Sepia']];
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

  document.querySelector('[data-action="add-chip"]').addEventListener('click', (event) => {
    place(menu, event.currentTarget);
    menu.querySelector('[data-field]')?.focus();
    event.stopPropagation();
  });

  menu.addEventListener('click', (event) => {
    const field = event.target.closest('[data-field]')?.dataset.field;
    if (!field) return;
    menu.hidden = true;
    const fresh = field === 'stars' ? { is: 'stars', least: 3 }
      : field === 'taken' ? { is: 'taken', from: '', to: '' }
      : { is: field, values: [] };
    void openEditor(fresh, null, document.querySelector('[data-action="add-chip"]'));
  });

  bar.addEventListener('click', (event) => {
    const drop = event.target.closest('[data-drop]');
    if (drop) {
      const dropped = Number(drop.dataset.drop);
      const next = chips().slice();
      next.splice(dropped, 1);
      if (editing && editing.index !== null) {
        if (editing.index === dropped) closeEditor();
        else if (editing.index > dropped) editing.index -= 1;
      }
      commit(next);
      return;
    }
    const pill = event.target.closest('[data-chip]');
    if (pill) {
      const index = Number(pill.dataset.chip);
      void openEditor({ ...chips()[index] }, index, pill);
    }
  });

  document.addEventListener('click', (event) => {
    if (!editor.hidden && !editor.contains(event.target)
        && !event.target.closest('[data-chip]') && !event.target.closest('[data-field]')) {
      closeEditor();
    }
    if (!menu.hidden && !menu.contains(event.target)) menu.hidden = true;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || (editor.hidden && menu.hidden)) return;
    closeEditor();
    menu.hidden = true;
    event.preventDefault();
    event.stopImmediatePropagation();
  });

  return Object.freeze({ render, describe, close: closeEditor });
}
