// The Labels section: the words you have taught, counted, tilde-first.
// Clicking a word browses it; refining happens inside that view, where Y
// anchors and N excludes — the vocabulary is exactly what you have
// answered for, so an untaught library shows no section at all.
import { why } from '../kit/why.js';
import { showMenu, hideMenu } from '../kit/menu.js';
import { acceptDrops } from '../kit/drop.js';
import { icon } from '../kit/icons.js';

export function createLabelsPanel({ product, update, browse, ask, notify, undo }) {
  const section = document.querySelector('[data-labels-section]');
  const list = document.querySelector('[data-labels-list]');
  const menu = document.querySelector('[data-label-menu]');

  async function refresh() {
    try {
      update({ labels: await product.labels() });
    } catch {
      // a library with no taught words simply has none
    }
  }

  let seen = null;
  let wornSeen = '';
  const worn = (state) => (state.chips || []).filter((c) => c.is === 'label' && !c.not).flatMap((c) => c.values).join('\u0001');
  function render(state) {
    if (seen === state.labels && wornSeen === worn(state)) return;
    seen = state.labels;
    wornSeen = worn(state);
    const active = new Set(wornSeen ? wornSeen.split('\u0001') : []);
    const held = state.labels || [];
    section.hidden = held.length === 0;
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...held.map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row' + (active.has(entry.term) ? ' is-active' : '');
      row.dataset.term = entry.term;
      row.title = `${entry.term} — a word you taught; Y and N inside its view refine it. About ${entry.count.toLocaleString()}: the count settles as the word is taught`;
      const mark = icon('label');
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

  list.addEventListener('click', (event) => {
    const row = event.target.closest('[data-term]');
    if (row) browse(row.dataset.term);
  });

  // The same verbs an album has, in the same menu grammar: a taught word
  // can be called something else or forgotten (with the way back).
  list.addEventListener('contextmenu', (event) => {
    const row = event.target.closest('[data-term]');
    if (!row) return;
    event.preventDefault();
    menu.dataset.term = row.dataset.term;
    showMenu(menu, event.clientX, event.clientY);
  });
  menu.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    const word = menu.dataset.term;
    hideMenu(menu);
    if (!action || !word) return;
    try {
      if (action === 'rename-label') {
        const row = list.querySelector(`[data-term="${CSS.escape(word)}"]`);
        const called = await ask('Rename to', row || list, word, { not: (list.querySelectorAll('[data-term]') && [...list.querySelectorAll('[data-term]')].map((r) => r.dataset.term).filter((t) => t !== word)) });
        if (!called || called === word) return;
        await product.renameLabel(word, called);
        await refresh();
        notify(`“${word}” renamed to “${called}”.`);
      }
      if (action === 'forget-label') {
        const gone = await product.forgetLabel(word);
        await refresh();
        undo.show(`“${word}” forgotten.`, async () => { await product.rememberAlbum(gone.id); await refresh(); });
      }
    } catch (error) {
      notify(why(error));
    }
  });

  // A drop on a word teaches it: these photographs are this word.
  acceptDrops(list, '[data-term]', {
    drop: async (row, ids) => {
      try {
        await product.teach(row.dataset.term, ids, true);
        await refresh();
        notify(`${ids.length === 1 ? 'This photograph' : `${ids.length.toLocaleString()} photographs`} taught as “${row.dataset.term}”.`);
      } catch (error) {
        notify(why(error));
      }
    },
  });

  return Object.freeze({ refresh, render });
}
