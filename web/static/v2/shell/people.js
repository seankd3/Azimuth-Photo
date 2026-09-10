// The People section and the face wall: everyone the library can tell
// apart. The introduced sit as face rows under their names; a few Someones
// wait below them, and the rest live on the wall — a workspace stage of
// big faces where introducing many people is fast. Clicking anyone
// browses their photographs — seeing them is how you decide who they are.
// Counts wear the tilde: membership is the face model's answer until
// calibration.

const WAITING = 3;   // Someones shown in the sidebar before the wall takes over

import { acceptDrops } from '../kit/drop.js';
import { why } from '../kit/why.js';
import { columnsOf } from '../kit/days.js';
import { emptyState } from '../lens/library.js';
import { icon } from '../kit/icons.js';

export function createPeoplePanel({ product, read, update, notify, undo, browse, renamed, ask }) {
  const section = document.querySelector('[data-people-section]');
  const list = document.querySelector('[data-people-list]');
  const stage = document.querySelector('[data-people-stage]');
  // The wall's cursor: which card the keys act on.
  let cursor = -1;

  let maybe = [];
  async function refresh() {
    try {
      maybe = await product.maybeSame();
    } catch {
      maybe = [];
    }
    try {
      update({ people: await product.people() });
    } catch (error) {
      // A library without faces yet has nobody to list; anything else is
      // said, because a silent read looks exactly like no faces.
      if (!/no faces|no people/i.test(String(error.message))) notify(why(error));
    }
  }

  function face(entry, className) {
    const sample = entry.samples?.[0];
    if (!sample) return icon('person', className === 'face-card-face' ? 'icon icon-big' : 'icon');
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
  let wornSeen = '';
  const worn = (state) => (state.chips || []).filter((c) => c.is === 'person' && !c.not).flatMap((c) => c.values).join('\u0001');
  function render(state) {
    if (stage.hidden !== (state.view !== 'people')) stage.hidden = state.view !== 'people';
    if (seen === state.people && seenView === state.view && wornSeen === worn(state)) return;
    seen = state.people;
    seenView = state.view;
    wornSeen = worn(state);
    const held = state.people || [];
    section.hidden = held.length === 0;
    renderRows(held, new Set(wornSeen ? wornSeen.split('\u0001') : []));
    if (!stage.hidden) renderWall(held);
  }

  function renderRows(held, active = new Set()) {
    if (section.hidden) {
      list.replaceChildren();
      return;
    }
    const named = held.filter((entry) => entry.settled);
    const waiting = held.filter((entry) => !entry.settled);
    const rows = [...named, ...waiting.slice(0, WAITING)].map((entry) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'side-row person-row' + (active.has(entry.term) ? ' is-active' : '');
      row.dataset.term = entry.term;
      row.dataset.person = entry.person;
      row.dataset.settled = entry.settled ? '1' : '0';
      row.title = (!entry.settled
        ? 'Someone the library keeps seeing — click to see them; N or right-click names them'
        : `${entry.term} — N or right-click renames`) + `. About ${entry.count.toLocaleString()}: the count settles as faces are read`;
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
    leaf.textContent = left > 0
      ? `Introduce ${left} more…`
      : `All ${held.length.toLocaleString()} ${held.length === 1 ? 'person' : 'people'}…`;
    door.append(icon('person'), leaf);
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
    // The questions first: two faces the library thinks are one person.
    // Yes heals the split as naming does; No keeps them apart for good.
    const asks = maybe.slice(0, 3).map((pair) => {
      const card = document.createElement('div');
      card.className = 'same-card';
      card.tabIndex = -1;
      card.setAttribute('role', 'group');
      card.setAttribute('aria-label', `Same person? ${pair.a.term} and ${pair.b.term}`);
      const faces = document.createElement('div');
      faces.className = 'same-faces';
      for (const side of [pair.a, pair.b]) {
        const who = document.createElement('div');
        who.className = 'same-side';
        who.append(face({ samples: side.samples }, 'face-card-face'));
        const name = document.createElement('p');
        name.className = 'face-card-name';
        name.textContent = side.term;
        who.append(name);
        faces.append(who);
      }
      const question = document.createElement('p');
      question.className = 'same-question';
      question.textContent = 'Same person?';
      const yes = document.createElement('button');
      yes.type = 'button';
      yes.className = 'primary-button';
      // Two introduced people becoming one: say which name survives.
      const survives = (pair.a.settled || pair.b.settled) && pair.a.term !== pair.b.term;
      yes.textContent = survives ? `Yes — both are “${pair.a.settled ? pair.a.term : pair.b.term}”` : 'Yes';
      yes.title = 'The same person (Y)';
      const no = document.createElement('button');
      no.type = 'button';
      no.className = 'quiet-button';
      no.textContent = 'No';
      no.title = 'Two people — never ask again (N)';
      yes.addEventListener('click', () => void sameAs(pair, yes));
      no.addEventListener('click', () => void apart(pair));
      card.addEventListener('keydown', (event) => {
        if (event.key === 'y' || event.key === 'Y') { void sameAs(pair, yes); event.preventDefault(); event.stopPropagation(); }
        if (event.key === 'n' || event.key === 'N') { void apart(pair); event.preventDefault(); event.stopPropagation(); }
        // The card's own arrows are the browser's; the wall's cursor must
        // not pull focus away mid-question.
        if (event.key.startsWith('Arrow')) event.stopPropagation();
      });
      const answers = document.createElement('div');
      answers.className = 'same-answers';
      answers.append(yes, no);
      card.append(faces, question, answers);
      return card;
    });
    // A rewrite keeps the keyboard where it was: on a question, the next
    // question (else the wall); on the wall, the wall's cursor.
    const focused = document.activeElement;
    const inQuestion = stage.contains(focused) && focused.closest('.same-card');
    const onWall = stage.contains(focused) && focused.closest('.face-card');
    stage.replaceChildren(...asks, ...order.map((entry, index) => {
      const card = document.createElement('div');
      card.className = 'face-card' + (index === cursor ? ' is-focus' : '');
      // One cursor: the picture button takes the keyboard (it has a real
      // name); the ring is the browser's own focus; the whole card is the
      // way to the person.
      card.addEventListener('click', (event) => { if (!event.target.closest('button')) browse(entry.term); });
      card.dataset.term = entry.term;
      card.dataset.person = entry.person;
      card.dataset.index = index;
      card.dataset.settled = entry.settled ? '1' : '0';
      const look = document.createElement('button');
      look.type = 'button';
      look.className = 'face-card-look';
      look.title = `See ${entry.term}'s photographs`;
      look.setAttribute('aria-label', `${entry.term}, about ${entry.count.toLocaleString()} photographs — see them`);
      look.tabIndex = index === cursor ? 0 : -1;
      look.addEventListener('focus', () => { cursor = index; markWall(false); });
      look.append(face(entry, 'face-card-face'));
      look.addEventListener('click', () => browse(entry.term));
      const name = document.createElement('p');
      name.className = 'face-card-name';
      name.textContent = entry.term;
      const count = document.createElement('p');
      count.className = 'face-card-count';
      count.textContent = `~${entry.count.toLocaleString()} photographs`;
      const call = document.createElement('button');
      call.type = 'button';
      call.className = 'quiet-button face-card-name-button';
      call.textContent = entry.settled ? 'Rename' : 'Name…';
      call.addEventListener('click', () => void introduce(entry.person, entry.term, entry.settled, call));
      card.append(look, name, count, call);
      return card;
    }));
    if (inQuestion) (stage.querySelector('.same-card .primary-button') || stage.querySelector('.face-card-look'))?.focus({ preventScroll: true });
    else if (onWall) { if (cursor < 0) cursor = 0; markWall(); }
  }

  function markWall(focus = true) {
    for (const card of stage.querySelectorAll('.face-card')) {
      const here = Number(card.dataset.index) === cursor;
      card.classList.toggle('is-focus', here);
      card.querySelector('.face-card-look').tabIndex = here ? 0 : -1;
    }
    const held = stage.querySelector('.face-card.is-focus');
    held?.scrollIntoView({ block: 'nearest' });
    if (focus) held?.querySelector('.face-card-look')?.focus({ preventScroll: true });
  }

  function key(event) {
    // The wall's keys: arrows move by card and row, Enter browses, N names,
    // Esc leaves -- introducing thirty Someones never needs the mouse. Y
    // from anywhere on the wall answers the first question; N is the
    // card's own key, because on the wall N names.
    if ((event.key === 'y' || event.key === 'Y') && maybe.length && !event.ctrlKey && !event.metaKey) {
      void sameAs(maybe[0], stage.querySelector('.same-card .primary-button'));
      return true;
    }
    const cards = stage.querySelectorAll('.face-card');
    if (!cards.length) return false;
    const across = columnsOf(cards);
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

  async function sameAs(pair, anchor) {
    // The name both will answer to: a side already introduced gives it;
    // with neither introduced, the wall asks for one.
    let called = pair.a.settled ? pair.a.term : pair.b.settled ? pair.b.term : null;
    if (!called) called = await ask('One person — their name', anchor, '');
    if (!called) return;
    try {
      // The way back replays the log: every face named by this Yes answers
      // to what it answered to before, whichever side and however many.
      const done = await product.samePeople(pair.a.exemplar, pair.b.exemplar, called);
      undo.show(`“${pair.a.term}” and “${pair.b.term}” are one: “${called}”.`, async () => {
        await product.unnameSince(done.since, done.until);
        await renamed();
      });
      await renamed();
    } catch (error) {
      notify(why(error));
    }
  }

  async function apart(pair) {
    try {
      await product.keepApart(pair.a.exemplar, pair.b.exemplar);
      maybe = maybe.filter((m) => m !== pair);
      renderWall(read().people || []);
      notify(`“${pair.a.term}” and “${pair.b.term}” stay two people.`);
    } catch (error) {
      notify(why(error));
    }
  }

  async function introduce(exemplar, current, settled, anchor) {
    const called = await ask(settled ? 'Rename to' : 'Name this person', anchor, settled ? current : '');
    if (!called || called === current) return;
    try {
      await product.namePerson(exemplar, called);
      const said = `“${called}” named. Their photographs gather as the library reads.`;
      // A rename is taken back by naming them what they were; a first
      // naming by taking the name back, so the face is a Someone again.
      undo.show(said, async () => {
        if (settled) await product.namePerson(exemplar, current);
        else await product.unnamePerson(exemplar);
        await renamed();
      });
      // The lane regroups and the pulse says so; the shelf re-reads then.
      void renamed();
    } catch (error) {
      notify(why(error));
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
  acceptDrops(list, '.person-row', {
    judge: () => 'A person is found by their face, not filed by hand',
    drop: () => {},
  });
  list.addEventListener('contextmenu', nameFrom);
  stage.addEventListener('contextmenu', nameFrom);
  list.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() !== 'n' || event.ctrlKey || event.metaKey) return;
    nameFrom(event);
  });

  return Object.freeze({ refresh, render, key });
}
