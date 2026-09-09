// The People section and the face wall: everyone the library can tell
// apart. The introduced sit as face rows under their names; a few Someones
// wait below them, and the rest live on the wall — a workspace stage of
// big faces where introducing many people is fast. Clicking anyone
// browses their photographs — seeing them is how you decide who they are.
// Counts wear the tilde: membership is the face model's answer until
// calibration.

const WAITING = 3;   // Someones shown in the sidebar before the wall takes over

import { emptyState } from '../lens/library.js';

export function createPeoplePanel({ product, _read, update, notify, undo, browse, renamed, ask }) {
  const section = document.querySelector('[data-people-section]');
  const list = document.querySelector('[data-people-list]');
  const stage = document.querySelector('[data-people-stage]');
  // The wall's cursor: which card the keys act on.
  let cursor = -1;

  async function refresh() {
    try {
      update({ people: await product.people() });
    } catch (error) {
      // A library without faces yet has nobody to list; anything else is
      // said, because a silent read looks exactly like no faces.
      if (!/no faces|no people/i.test(String(error.message))) notify(error.message);
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
      row.dataset.settled = entry.settled ? '1' : '0';
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
    const leaf = document.createElement('span');
    leaf.className = 'leaf';
    leaf.textContent = left > 0 ? `Introduce ${left} more…` : 'All people…';
    const total = document.createElement('span');
    total.className = 'set-count';
    total.textContent = held.length.toLocaleString();
    door.append(leaf, total);
    door.addEventListener('click', () => update({ view: 'people', selected: null, selectedIndex: null }));
    rows.push(door);
    list.replaceChildren(...rows);
  }

  function renderWall(held) {
    if (!held.length) {
      stage.replaceChildren(emptyState({
        emptyTitle: 'No faces yet.',
        emptyCopy: 'Faces appear here as the library reads your photographs.',
      }));
      return;
    }
    const order = [...held.filter((e) => !e.settled), ...held.filter((e) => e.settled)];
    cursor = Math.min(cursor, order.length - 1);
    stage.replaceChildren(...order.map((entry, index) => {
      const card = document.createElement('div');
      card.className = 'face-card' + (index === cursor ? ' is-focus' : '');
      card.dataset.term = entry.term;
      card.dataset.person = entry.person;
      card.dataset.index = index;
      card.dataset.settled = entry.settled ? '1' : '0';
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
      call.addEventListener('click', () => void introduce(entry.person, entry.term, entry.settled, call));
      card.append(look, name, count, call);
      return card;
    }));
  }

  function markWall() {
    for (const card of stage.querySelectorAll('.face-card')) {
      card.classList.toggle('is-focus', Number(card.dataset.index) === cursor);
    }
    stage.querySelector('.face-card.is-focus')?.scrollIntoView({ block: 'nearest' });
  }

  function key(event) {
    // The wall's keys: arrows move by card and row, Enter browses, N names,
    // Esc leaves -- introducing thirty Someones never needs the mouse.
    const cards = stage.querySelectorAll('.face-card');
    if (!cards.length) return false;
    const across = Math.max(1, Math.floor(stage.clientWidth / 178));
    const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -across, ArrowDown: across };
    if (event.key in moves) {
      cursor = Math.max(0, Math.min(cards.length - 1, (cursor < 0 ? 0 : cursor + moves[event.key])));
      markWall();
      return true;
    }
    if (event.key === 'Home') { cursor = 0; markWall(); return true; }
    if (event.key === 'End') { cursor = cards.length - 1; markWall(); return true; }
    const card = cursor >= 0 ? cards[cursor] : null;
    if (!card) return false;
    if (event.key === 'Enter') { browse(card.dataset.term); return true; }
    if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey) {
      void introduce(card.dataset.person, card.dataset.term, card.dataset.settled === '1', card.querySelector('.face-card-name-button'));
      return true;
    }
    return false;
  }

  async function introduce(exemplar, current, settled, anchor) {
    const called = await ask(settled ? 'Rename to' : 'Name this person', anchor, settled ? current : '');
    if (!called || called === current) return;
    try {
      await product.namePerson(exemplar, called);
      const said = `“${called}” — the library will gather their photographs now.`;
      // A rename can be taken back by naming them what they were; a first
      // naming has no former name to return to.
      if (settled) undo.show(said, async () => { await product.namePerson(exemplar, current); await renamed(); });
      else notify(said);
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

  // One verb, one gesture, in both homes: right-click names or renames on
  // the shelf and on the wall alike.
  const nameFrom = (event) => {
    const row = event.target.closest('[data-term]');
    if (!row || !row.dataset.person) return;
    event.preventDefault();
    const settled = row.dataset.settled === '1' || (!row.dataset.settled && !row.dataset.term.startsWith('Someone'));
    void introduce(row.dataset.person, row.dataset.term, settled, row);
  };
  list.addEventListener('contextmenu', nameFrom);
  stage.addEventListener('contextmenu', nameFrom);

  return Object.freeze({ refresh, render, key });
}
