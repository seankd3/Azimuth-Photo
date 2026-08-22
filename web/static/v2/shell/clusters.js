// The clusters shelf: what the library proposes about itself. Each row is a
// live smart collection waiting to be kept — clicking browses it as chips,
// right-click keeps it, and a kept name stops being proposed because the
// collection now answers for it. Counts wear the tilde on purpose: a
// proposal is the space talking, not a fact, until calibration teaches a
// label its threshold.
export function createClustersPanel({ product, read, update, notify, browse, kept, ask }) {
  const section = document.querySelector('[data-clusters-section]');
  const list = document.querySelector('[data-clusters-list]');
  const menu = document.querySelector('[data-cluster-menu]');

  async function refresh() {
    try {
      update({ clusters: await product.clusters() });
    } catch {
      // proposing is optional; a library without vectors simply has none
    }
  }

  let seen = null;
  function render(state) {
    if (seen === state.clusters) return;
    seen = state.clusters;
    const held = state.clusters || [];
    section.hidden = held.length === 0;
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...held.map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'collection-row cluster-row';
      if (entry.person) {
        // A face group nobody has introduced yet: the row exists to be named.
        row.dataset.person = entry.person;
        row.title = 'Someone the library keeps seeing — click to name them';
      } else {
        row.dataset.cluster = entry.term;
        row.title = `${entry.term} — proposed from the photographs themselves`;
      }
      let mark;
      const face = entry.people && entry.samples?.[0];
      if (face) {
        // The mark is the person: their own face, cropped from the tile it
        // was found on.
        mark = document.createElement('img');
        mark.className = 'cluster-face';
        mark.src = face.tile;
        mark.alt = '';
        mark.decoding = 'async';
        if (face.view) mark.style.objectViewBox = face.view;
      } else {
        mark = document.createElement('span');
        mark.className = 'smart-mark';
        mark.textContent = entry.people ? '◉' : '◇';
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

  async function introduce(exemplar, anchor) {
    const called = await ask('Name this person', anchor);
    if (!called) return;
    try {
      await product.namePerson(exemplar, called);
      notify(`“${called}” — the library will gather their photographs now.`);
      // The groups rewrite on the rank lane; a beat later the name is real.
      setTimeout(() => void kept(), 3000);
    } catch (error) {
      notify(error.message);
    }
  }

  list.addEventListener('click', (event) => {
    const someone = event.target.closest('[data-person]');
    if (someone) {
      void introduce(someone.dataset.person, someone);
      return;
    }
    const row = event.target.closest('[data-cluster]');
    if (row) browse(row.dataset.cluster);
  });

  list.addEventListener('contextmenu', (event) => {
    const someone = event.target.closest('[data-person]');
    if (someone) {
      event.preventDefault();
      void introduce(someone.dataset.person, someone);
      return;
    }
    const row = event.target.closest('[data-cluster]');
    if (!row) return;
    event.preventDefault();
    menu.dataset.cluster = row.dataset.cluster;
    menu.hidden = false;
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;
  });

  menu.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    const term = menu.dataset.cluster;
    menu.hidden = true;
    if (action !== 'keep-cluster' || !term) return;
    try {
      await product.createCollection(term, [{ is: 'alike', values: [term] }]);
      notify(`“${term}” is now a collection — live, and yours to rename.`);
      await kept();
    } catch (error) {
      notify(error.message);
    }
  });

  document.addEventListener('click', (event) => {
    if (!menu.hidden && !menu.contains(event.target)) menu.hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || menu.hidden) return;
    menu.hidden = true;
    event.preventDefault();
    event.stopImmediatePropagation();
  });

  return Object.freeze({ refresh, render });
}
