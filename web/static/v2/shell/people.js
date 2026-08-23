// The People section: everyone the library can tell apart. The introduced
// sit as face rows under their names; the rest are Someones. Clicking
// anyone browses their photographs — seeing them is how you decide who
// they are — and naming or renaming is one right-click away. Counts wear
// the tilde: membership is the face model's answer until calibration.
export function createPeoplePanel({ product, read, update, notify, browse, renamed, ask }) {
  const section = document.querySelector('[data-people-section]');
  const list = document.querySelector('[data-people-list]');

  async function refresh() {
    try {
      update({ people: await product.people() });
    } catch {
      // a library without faces yet simply has nobody to list
    }
  }

  let seen = null;
  function render(state) {
    if (seen === state.people) return;
    seen = state.people;
    const held = state.people || [];
    section.hidden = held.length === 0;
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...held.map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row person-row';
      row.dataset.term = entry.term;
      row.dataset.person = entry.person;
      row.title = !entry.settled
        ? 'Someone the library keeps seeing — click to see them, right-click to name them'
        : `${entry.term} — right-click to rename`;
      const face = entry.samples?.[0];
      let mark;
      if (face) {
        mark = document.createElement('img');
        mark.className = 'person-face';
        mark.src = face.tile;
        mark.alt = '';
        mark.decoding = 'async';
        if (face.view) mark.style.objectViewBox = face.view;
      } else {
        mark = document.createElement('span');
        mark.className = 'smart-mark';
        mark.textContent = '◉';
      }
      const name = document.createElement('span');
      name.className = 'leaf';
      name.textContent = entry.term;
      const count = document.createElement('span');
      count.className = 'set-count';
      count.textContent = `~${entry.count.toLocaleString()}`;
      row.append(mark, name, count);
      return row;
    }));
  }

  async function introduce(exemplar, current, anchor) {
    const called = await ask(current.startsWith('Someone') ? 'Name this person' : 'Rename to', anchor,
                             current.startsWith('Someone') ? '' : current);
    if (!called || called === current) return;
    try {
      await product.namePerson(exemplar, called);
      notify(`“${called}” — the library will gather their photographs now.`);
      setTimeout(() => void renamed(), 3000);
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
