// The Labels section: the words you have taught, counted, tilde-first.
// Clicking a word browses it; refining happens inside that view, where Y
// anchors and N excludes — the vocabulary is exactly what you have
// answered for, so an untaught library shows no section at all.
import { icon } from '../kit/icons.js';

export function createLabelsPanel({ product, update, browse }) {
  const section = document.querySelector('[data-labels-section]');
  const list = document.querySelector('[data-labels-list]');

  async function refresh() {
    try {
      update({ labels: await product.labels() });
    } catch {
      // a library with no taught words simply has none
    }
  }

  let seen = null;
  function render(state) {
    if (seen === state.labels) return;
    seen = state.labels;
    const held = state.labels || [];
    section.hidden = held.length === 0;
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...held.map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row';
      row.dataset.term = entry.term;
      row.title = `${entry.term} — a word you taught; Y and N inside its view refine it`;
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

  return Object.freeze({ refresh, render });
}
