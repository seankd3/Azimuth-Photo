// The People section and the face wall: everyone the library can tell
// apart. The introduced sit as face rows under their names; a few Someones
// wait below them, and the rest live on the wall — a workspace stage of
// big faces where introducing many people is fast. Clicking anyone
// browses their photographs — seeing them is how you decide who they are.
// Counts wear the tilde: membership is the face model's answer until
// calibration.

const WAITING = 3;   // Someones shown in the sidebar before the wall takes over

export function createPeoplePanel({ product, _read, update, notify, browse, renamed, ask }) {
  const section = document.querySelector('[data-people-section]');
  const list = document.querySelector('[data-people-list]');
  const stage = document.querySelector('[data-people-stage]');

  async function refresh() {
    try {
      update({ people: await product.people() });
    } catch {
      // a library without faces yet simply has nobody to list
    }
  }

  function face(entry, className) {
    const sample = entry.samples?.[0];
    if (!sample) {
      const mark = document.createElement('span');
      mark.className = 'smart-mark';
      mark.textContent = '◉';
      return mark;
    }
    const mark = document.createElement('img');
    mark.className = className;
    mark.src = sample.tile;
    mark.alt = '';
    mark.decoding = 'async';
    if (sample.view) mark.style.objectViewBox = sample.view;
    return mark;
  }

  let seen = null;
  let seenView = null;
  function render(state) {
    if (stage.hidden !== (state.view !== 'people')) stage.hidden = state.view !== 'people';
    if (seen === state.people && seenView === state.view) return;
    seen = state.people;
    seenView = state.view;
    const held = state.people || [];
    section.hidden = held.length === 0;
    renderRows(held);
    if (!stage.hidden) renderWall(held);
  }

  function renderRows(held) {
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    const named = held.filter((entry) => entry.settled);
    const waiting = held.filter((entry) => !entry.settled);
    const rows = [...named, ...waiting.slice(0, WAITING)].map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row person-row';
      row.dataset.term = entry.term;
      row.dataset.person = entry.person;
      row.title = !entry.settled
        ? 'Someone the library keeps seeing — click to see them, right-click to name them'
        : `${entry.term} — right-click to rename`;
      const name = document.createElement('span');
      name.className = 'leaf';
      name.textContent = entry.term;
      const count = document.createElement('span');
      count.className = 'set-count';
      count.textContent = `~${entry.count.toLocaleString()}`;
      row.append(face(entry, 'person-face'), name, count);
      return row;
    });
    // The wall's door: the rest of the Someones live there, and so does
    // everyone at a size a face can actually be recognized at.
    const door = document.createElement('button');
    door.type = 'button';
    door.className = 'side-row people-door';
    const left = waiting.length - Math.min(waiting.length, WAITING);
    door.textContent = left > 0 ? `Introduce ${left} more…` : 'All people…';
    door.addEventListener('click', () => update({ view: 'people', selected: null, selectedIndex: null }));
    rows.push(door);
    list.replaceChildren(...rows);
  }

  function renderWall(held) {
    const order = [...held.filter((e) => !e.settled), ...held.filter((e) => e.settled)];
    stage.replaceChildren(...order.map((entry) => {
      const card = document.createElement('div');
      card.className = 'face-card';
      card.dataset.term = entry.term;
      const look = document.createElement('button');
      look.type = 'button';
      look.className = 'face-card-look';
      look.title = `See ${entry.term}'s photographs`;
      look.append(face(entry, 'face-card-face'));
      look.addEventListener('click', () => browse(entry.term));
      const name = document.createElement('p');
      name.className = 'face-card-name';
      name.textContent = entry.term;
      const count = document.createElement('p');
      count.className = 'face-card-count';
      count.textContent = `~${entry.count.toLocaleString()} photos`;
      const call = document.createElement('button');
      call.type = 'button';
      call.className = 'quiet-button face-card-name-button';
      call.textContent = entry.settled ? 'Rename' : 'Name…';
      call.addEventListener('click', () => void introduce(entry.person, entry.term, call));
      card.append(look, name, count, call);
      return card;
    }));
  }

  async function introduce(exemplar, current, anchor) {
    const called = await ask(current.startsWith('Someone') ? 'Name this person' : 'Rename to', anchor,
                             current.startsWith('Someone') ? '' : current);
    if (!called || called === current) return;
    try {
      await product.namePerson(exemplar, called);
      notify(`“${called}” — the library will gather their photographs now.`);
      // The lane regroups and the pulse says so; the shelf re-reads then.
      void renamed();
    } catch (error) {
      notify(error.message);
    }
  }

  list.addEventListener('click', (event) => {
    const row = event.target.closest('[data-term]');
    if (row) browse(row.dataset.term);
  });

  list.addEventListener('contextmenu', (event) => {
    const row = event.target.closest('[data-term]');
    if (!row || !row.dataset.person) return;
    event.preventDefault();
    void introduce(row.dataset.person, row.dataset.term, row);
  });

  return Object.freeze({ refresh, render });
}
