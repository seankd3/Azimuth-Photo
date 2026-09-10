// Rank: you pick the best of a set drawn from what you are looking at, the
// round is recorded whole, and the order of everything follows. Everything
// about the stage serves one click being honest and instant: every candidate
// is shown at the same area so shape never votes, the next photographs are
// already decoded before they are needed, the pick is seen for a beat and
// then leaves with the longest-standing other (in a pair, both), and Undo
// takes the round back and puts the set back as it was.

// How many at once -> the cells they sit in. Five sizes, all the person
// needs: a duel, and four grids up to the twelve a maximized window holds.
const SIZES = { 2: [2, 1], 4: [2, 2], 6: [3, 2], 9: [3, 3], 12: [4, 3] };
// Identities kept out of the next sets, so a frame does not come straight
// back round -- never more than the scope can spare, so a small album is
// not dead-ended by its own memory.
const RECENT = 48;
// Extreme shapes are clamped for sizing only, so one panorama cannot shrink
// every other card in the set.
const ASPECT_MIN = 0.4;
const ASPECT_MAX = 2.6;

import { why } from '../kit/why.js';
import { emptyState } from '../lens/library.js';
import { recall, remember as keep } from '../kit/remembered.js';

export function createRankWorkflow({ product, read, update, notify, undo, cull, onLeave, onLook, viewOf, describe }) {
  const stage = document.querySelector('[data-rank]');
  const MODES = ['learn', 'random', 'diverse', 'tournament'];
  const MODE_KEY = 'azimuth.rank-mode';
  const SIZE_KEY = 'azimuth.rank-size';
  const state = {
    size: SIZES[recall(SIZE_KEY, 9)] ? recall(SIZE_KEY, 9) : 9,
    set: [], age: [], buffer: [], recent: [], selected: -1,
    rounds: 0, judged: 0, earned: 0, total: 0, busy: false, filling: null, generation: 0,
    answered: false, queued: null,
    // Reading order of the cards on stage, set by the layout: order[k] is
    // the set index of the k-th card left to right, top to bottom; rows
    // hold the same indices row by row, for the arrows.
    order: [], rows: [],
    mode: MODES.includes(recall(MODE_KEY, 'learn')) ? recall(MODE_KEY, 'learn') : 'learn',
  };
  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function isOpen() {
    return read().view === 'rank';
  }

  function remember(photos) {
    for (const photo of photos) state.recent.push(photo.hash);
    const cap = Math.min(RECENT, Math.max(0, state.total - state.size * 2));
    if (state.recent.length > cap) state.recent.splice(0, state.recent.length - cap);
  }

  function avoiding() {
    return [...new Set([...state.set.map((p) => p.hash), ...state.buffer.map((p) => p.hash), ...state.recent])];
  }

  async function ask(n) {
    // The whole answer comes back; the caller writes what it holds after its
    // own staleness check, so a superseded ask cannot smear old numbers.
    return product.rank({ n, view: viewOf(), avoid: avoiding(), mode: state.mode });
  }

  function accept(answer) {
    state.judged = answer.judged;
    state.earned = answer.earned || 0;
    state.total = answer.total;
    state.answered = true;
  }

  function preload(photo) {
    // Decoded before it is shown, so the swap is one frame. The browser
    // keeps the decoded bitmap for an element that stays referenced.
    const image = new Image();
    image.decoding = 'async';
    image.src = sourceFor(photo);
    photo.warm = image;
    return image.decode().catch(() => {});
  }

  function sourceFor(photo) {
    // The grid tile is 1,024 px; a card wider than that on this screen shows
    // the loupe when one exists — a pair on a large window. Past a pair the
    // grid tile is the answer: a 4,096 px loupe decodes in the hundreds of
    // milliseconds, and two of them per pick was the lag between a click and
    // the next set on a 1.5x display.
    const cells = SIZES[state.size] || SIZES[9];
    const wide = state.size <= 2 && (stage.clientWidth / cells[0]) * window.devicePixelRatio > 1024;
    return (wide && photo.loupe) || photo.tile;
  }

  async function fill() {
    // Keep one set's worth in hand, asked for in the background after every
    // change and never twice at once.
    if (state.filling || state.buffer.length >= state.size || !isOpen()) return;
    const generation = state.generation;
    state.filling = (async () => {
      try {
        const answer = await ask(state.size);
        if (generation !== state.generation) return;
        accept(answer);
        const onStage = new Set(state.set.map((p) => p.hash));
        for (const photo of answer.photos) {
          if (onStage.has(photo.hash) || state.buffer.some((p) => p.hash === photo.hash)) continue;
          state.buffer.push(photo);
          void preload(photo);
        }
        if (state.set.length < state.size && state.buffer.length) {
          // The set shrank while the well was dry; arrivals rejoin the
          // stage instead of waiting in hand for a set that cannot grow.
          while (state.set.length < state.size && state.buffer.length) {
            const photo = state.buffer.shift();
            state.set.push(photo);
            state.age.push(0);
            remember([photo]);
          }
          render();
        } else {
          renderProgress();
        }
      } catch (error) {
        notify(why(error));
      } finally {
        state.filling = null;
      }
    })();
    await state.filling;
  }

  async function load() {
    state.generation += 1;
    const generation = state.generation;
    state.buffer = [];
    state.set = [];
    state.age = [];
    state.selected = -1;
    state.queued = null;
    state.answered = false;
    render();
    try {
      let answer = await ask(state.size);
      if (generation !== state.generation) return;
      if (answer.photos.length < Math.min(state.size, 2) && state.recent.length) {
        // The memory window can swallow a small scope whole; forget what was
        // seen and ask once more before calling the well dry.
        state.recent = [];
        answer = await ask(state.size);
        if (generation !== state.generation) return;
      }
      accept(answer);
      state.set = answer.photos;
      state.age = answer.photos.map(() => 0);
      remember(answer.photos);
      // Decoded-before-shown, but never held hostage: a slow drive gets a
      // beat, then the stage paints and the stragglers pop in.
      await Promise.race([Promise.all(answer.photos.map(preload)), delay(180)]);
      if (generation !== state.generation) return;
      state.selected = state.set.length ? 0 : -1;
      render();
      void fill();
    } catch (error) {
      notify(why(error));
    }
  }

  async function open() {
    if (isOpen()) return;
    state.rounds = 0;
    update({ view: 'rank', selected: null, selectedIndex: null });
    await load();
  }

  async function close() {
    if (!isOpen()) return;
    state.generation += 1;
    state.set = [];
    state.buffer = [];
    update({ view: 'library' });
    await onLeave();
  }

  async function resize(n) {
    // The set grows or shrinks in place: the cards already judged against
    // each other stay; a smaller set lets its longest-standing cards go, a
    // larger one brings in what is in hand and asks for the rest.
    if (!SIZES[n]) return;
    state.size = n;
    keep(SIZE_KEY, n);
    if (!isOpen()) { render(); return; }
    const generation = state.generation;
    while (state.set.length > n) {
      let oldest = 0;
      for (let i = 1; i < state.age.length; i += 1) if (state.age[i] > state.age[oldest]) oldest = i;
      state.set.splice(oldest, 1);
      state.age.splice(oldest, 1);
    }
    while (state.set.length < n && state.buffer.length) {
      const photo = state.buffer.shift();
      state.set.push(photo);
      state.age.push(0);
      remember([photo]);
    }
    if (state.set.length < n) {
      try {
        const answer = await ask(n - state.set.length);
        if (generation !== state.generation) return;
        accept(answer);
        const onStage = new Set(state.set.map((p) => p.hash));
        for (const photo of answer.photos) {
          if (onStage.has(photo.hash) || state.set.length >= n) continue;
          state.set.push(photo);
          state.age.push(0);
          remember([photo]);
        }
      } catch (error) {
        notify(why(error));
      }
    }
    state.selected = Math.min(state.selected, state.set.length - 1);
    render();
    void fill();
  }

  async function remode(mode) {
    if (!MODES.includes(mode) || mode === state.mode) return;
    state.mode = mode;
    keep(MODE_KEY, mode);
    // The buffer was drawn under the old opinion; a fresh deal says the new one.
    state.buffer = [];
    if (isOpen()) await load();
    else render();
  }

  async function pick(index, { byMouse = false } = {}) {
    const winner = state.set[index];
    if (state.busy) {
      // The fastest part of the loop must never eat a keystroke: the pick
      // waits out the beat and lands, instead of vanishing.
      state.queued = { index, byMouse };
      return;
    }
    if (!winner || state.set.length < 2) return;
    state.busy = true;
    const generation = state.generation;
    // The pick is marked as it leaves; nothing waits on the mark. A round
    // is an act done hundreds of times a sitting, and a beat held before
    // the swap read as lag (the owner, 09-10).
    stage.querySelector(`[data-index="${index}"]`)?.classList.add('is-picked');
    const losers = state.set.filter((_, i) => i !== index);
    const before = { set: state.set.slice(), age: state.age.slice() };

    // The stage never waits for the write: the round records underneath
    // while the next set comes from photographs already decoded in hand.
    // The pick leaves the instant it is made; the arrivals fade in, so the
    // swap reads as motion rather than a stall.
    const writing = product.round(winner.id, losers.map((p) => p.id));
    writing.catch(() => {});
    state.rounds += 1;

    // The pick leaves, credited; so does the card that has sat through the
    // most rounds. In a pair that is both -- a decided pair is spent.
    let oldest = -1;
    for (let i = 0; i < state.age.length; i += 1) {
      if (i !== index && (oldest < 0 || state.age[i] > state.age[oldest])) oldest = i;
    }
    const leaving = oldest >= 0 ? [index, oldest] : [index];
    for (let i = 0; i < state.age.length; i += 1) state.age[i] += 1;

    // Every replacement in one motion: the buffer first, a fill already in
    // flight next, one ask for whatever is still missing — never a
    // round-trip per slot.
    const arrivals = leaving.map((slot) => [slot, state.buffer.shift() || null]);
    let missing = arrivals.filter(([, next]) => !next).length;
    if (missing && state.filling) {
      await state.filling;
      if (generation !== state.generation) { state.busy = false; state.queued = null; return; }  // undone or left meanwhile
      for (const entry of arrivals) {
        if (!entry[1]) entry[1] = state.buffer.shift() || null;
      }
      missing = arrivals.filter(([, next]) => !next).length;
    }
    if (missing) {
      try {
        const answer = await ask(missing);
        if (generation !== state.generation) { state.busy = false; state.queued = null; return; }
        accept(answer);
        const fresh = [...answer.photos];
        for (const entry of arrivals) {
          if (!entry[1]) entry[1] = fresh.shift() || null;
        }
      } catch {
        // nothing arrived; the set shrinks rather than blocking the pick
      }
    }
    for (const [slot, next] of arrivals) {
      if (next) {
        state.set[slot] = next;
        state.age[slot] = 0;
        remember([next]);
      }
    }
    // Nothing left to bring in: the set shrinks rather than repeating.
    const gone = arrivals.filter(([, next]) => !next).map(([slot]) => slot).sort((a, b) => b - a);
    for (const slot of gone) { state.set.splice(slot, 1); state.age.splice(slot, 1); }
    // A keyboard pick keeps its seat; a mouse pick leaves no cursor on the
    // stranger that just arrived there.
    state.selected = byMouse ? -1 : Math.min(index, state.set.length - 1);
    render();
    void fill();
    state.busy = false;

    // Undo waits only for its handle — the write that was already running.
    writing.then((recorded) => {
      const over = losers.length;
      undo.show(
        `${winner.tail.split('/').pop()} chosen over ${over === 1 ? 'one other' : `${over} others`}.`,
        async () => {
          await product.unround(recorded.decision);
          if (!isOpen()) { await onLeave(); return; }   // the grid behind may be sorted by it
          // Anything asked for around the retracted round is stale, numbers
          // included; the generation moves so an in-flight answer is dropped.
          state.generation += 1;
          state.rounds = Math.max(0, state.rounds - 1);
          state.set = before.set;
          state.age = before.age;
          render();
          state.buffer = [];
          void fill();
        },
      );
    }).catch((error) => {
      // The stage moved on; the round did not land. Said plainly — an
      // unrecorded pick that looked recorded would be worse than the pause.
      state.rounds = Math.max(0, state.rounds - 1);
      notify(`That pick was not recorded — ${error.message}`);
    });
    const queued = state.queued;
    state.queued = null;
    if (queued !== null) void pick(queued.index, queued);
  }

  function move(by) {
    // Along the reading order the layout wrote, not the set's slot order.
    if (!state.set.length) return;
    const order = state.order.length === state.set.length ? state.order : state.set.map((_, i) => i);
    const at = state.selected < 0 ? -1 : order.indexOf(state.selected);
    const next = at < 0 ? 0 : Math.min(order.length - 1, Math.max(0, at + by));
    state.selected = order[next];
    renderSelection();
  }

  function moveRow(by) {
    // The card in the row above or below at the same place across.
    if (!state.rows.length) { move(by * 3); return; }
    const where = state.rows.findIndex((row) => row.includes(state.selected));
    if (where < 0) { move(0); return; }
    const row = state.rows[Math.min(state.rows.length - 1, Math.max(0, where + by))];
    const across = state.rows[where].indexOf(state.selected);
    state.selected = row[Math.min(row.length - 1, across)];
    renderSelection();
  }

  // The keyboard's cursor wins whenever there is one; the mouse answers
  // only when nothing is selected, so X never rejects a card the hand
  // happened to be resting on.
  function underMouse() {
    const card = (state.selected >= 0 ? stage.querySelector(`[data-index="${state.selected}"]`) : null)
      || stage.querySelector('.rank-card:hover');
    return (card && state.set[Number(card.dataset.index)]) || null;
  }

  function key(event) {
    // The keyboard is the fast way through: a digit picks that card, arrows
    // move (or, in a pair, pick a side), Enter picks the selection.
    if (!isOpen()) return false;
    // Digits pick cards one to ten; the two keys past 0 on the same row
    // carry on to eleven and twelve, so every card at twelve-up has a key.
    const digit = event.key === '0' ? 10 : event.key === '-' ? 11 : event.key === '=' ? 12 : Number(event.key);
    if (Number.isInteger(digit) && digit >= 1 && digit <= state.set.length && event.key.length === 1) {
      void pick(state.order[digit - 1] ?? digit - 1);
      return true;
    }
    if (state.size === 2 && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      void pick(state.order[event.key === 'ArrowLeft' ? 0 : 1] ?? 0);
      return true;
    }
    // The two settings from the keys: [ and ] step how many at once, M
    // cycles how the set is drawn.
    if (event.key === '[' || event.key === ']') {
      const sizes = Object.keys(SIZES).map(Number).sort((a, b) => a - b);
      const at = sizes.indexOf(state.size);
      const next = sizes[Math.max(0, Math.min(sizes.length - 1, at + (event.key === ']' ? 1 : -1)))];
      if (next !== state.size) void resize(next);
      return true;
    }
    if ((event.key === 'm' || event.key === 'M') && !event.ctrlKey && !event.metaKey) {
      void remode(MODES[(MODES.indexOf(state.mode) + 1) % MODES.length]);
      return true;
    }
    if (event.key === 'ArrowLeft') { move(-1); return true; }
    if (event.key === 'ArrowRight') { move(1); return true; }
    if (event.key === 'ArrowUp') { moveRow(-1); return true; }
    if (event.key === 'ArrowDown') { moveRow(1); return true; }
    // A closer look at one card, without leaving the round: Z or F opens
    // the loupe on it, and Esc comes straight back.
    if ((event.key === 'z' || event.key === 'f' || event.key === 'Z' || event.key === 'F') && !event.ctrlKey && !event.metaKey) {
      const photo = underMouse();
      if (photo) { onLook(photo); return true; }
    }
    // The grid's per-photograph verbs, on the selected card (else the one
    // under the mouse): the same path the grid and the loupe take, with a
    // seat for how this stage's own rows take the change -- a turn re-packs
    // the shelf, a reject gives the seat to the next photograph in hand,
    // and Undo puts the set back as it was.
    const letter = event.key.length === 1 && !event.ctrlKey && !event.metaKey ? event.key.toLowerCase() : '';
    const verb = { r: event.shiftKey ? 'turnRight' : 'turnLeft', p: 'pick', u: 'clear', x: 'reject' }[letter];
    const photo = verb ? underMouse() : null;
    if (verb && photo && !state.busy) {
      const before = { set: state.set.slice(), age: state.age.slice() };
      let moved = 0;
      const tally = (by) => {
        const counts = read().counts;
        update({ counts: { ...counts, photos: Math.max(0, counts.photos - by), trash: Math.max(0, counts.trash + by) } });
      };
      void cull(verb, { ids: [photo.id], seat: {
        patched: (changed) => {
          for (const change of changed) for (const held of state.set) if (held.hash === change.subject) held[change.family] = change.after;
          render();     // a turn changed the aspect; the shelf re-packs around it
        },
        removed: (changed) => {
          // The rail keeps telling the truth: the same arithmetic nudge the
          // grid gives its counts when rows leave for the trash.
          moved = changed.reduce((total, change) => total + change.photos, 0);
          tally(moved);
          const at = state.set.indexOf(photo);
          if (at < 0) return;
          const next = state.buffer.shift() || null;
          if (next) { state.set[at] = next; state.age[at] = 0; remember([next]); }
          else { state.set.splice(at, 1); state.age.splice(at, 1); }
          state.selected = Math.min(state.selected, state.set.length - 1);
          render();
          void fill();
        },
        restored: async () => {
          if (moved) tally(-moved);
          if (!isOpen()) { await onLeave(); return; }   // the grid behind holds it again
          state.generation += 1;
          state.set = before.set;
          state.age = before.age;
          render();
          state.buffer = [];
          void fill();
        },
      } });
      return true;
    }
    if ((event.key === 'Enter' || event.key === ' ') && state.selected >= 0) { void pick(state.selected); return true; }
    return false;
  }

  // ---- drawing ----

  function aspectOf(photo) {
    const sideways = photo.rotate === 90 || photo.rotate === 270;
    const raw = photo.width > 0 && photo.height > 0
      ? (sideways ? photo.height / photo.width : photo.width / photo.height)
      : 1.5;
    return Math.min(ASPECT_MAX, Math.max(ASPECT_MIN, raw));
  }

  function layout() {
    // Every card presents the same pixel area at its own aspect. Uniform
    // cells with letterboxing showed a portrait at roughly half a landscape's
    // area, so the wider photograph won attention before taste entered -- a
    // bias written into durable ranking data.
    //
    // Equal area, but never uniform cells: a grid sizes every card for the
    // worst-fitting shape in it and drowns the stage in dead space. The
    // cards are packed on shelves instead — widest first, rows as tall as
    // their tallest member — and the area is the largest one whose packing
    // still fits the stage, found by bisection. The set stays one honest
    // comparison; the black around it goes to the photographs.
    const cards = [...stage.querySelectorAll('.rank-card')];
    if (!cards.length) return;
    const style = getComputedStyle(stage);
    const gap = parseFloat(style.gap) || 12;
    const padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    const padY = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const width = stage.clientWidth - padX;
    const height = stage.clientHeight - padY;
    if (width < 1 || height < 1) return;
    const aspects = cards.map((card) => aspectOf(state.set[Number(card.dataset.index)]));
    const order = cards.map((_, i) => i).sort((a, b) => aspects[b] - aspects[a]);

    function shelves(area) {
      // Greedy shelf packing at one card area; null when it cannot fit.
      const rows = [];
      let row = null;
      for (const i of order) {
        const w = Math.sqrt(area * aspects[i]);
        const h = Math.sqrt(area / aspects[i]);
        if (w > width) return null;
        if (!row || row.width + gap + w > width) {
          row = { members: [], width: -gap, tall: 0 };
          rows.push(row);
        }
        row.members.push({ i, w, h });
        row.width += gap + w;
        row.tall = Math.max(row.tall, h);
      }
      const used = rows.reduce((sum, r) => sum + r.tall, 0) + gap * (rows.length - 1);
      return used <= height ? rows : null;
    }

    let low = 1;
    let high = width * height;
    for (let step = 0; step < 40; step += 1) {
      const middle = (low + high) / 2;
      if (shelves(middle)) low = middle;
      else high = middle;
    }
    const rows = shelves(low);
    if (!rows) return;

    // Centred as a block: rows share the leftover height evenly, each row
    // centres its width, each card its own height within the row.
    const block = rows.reduce((sum, r) => sum + r.tall, 0) + gap * (rows.length - 1);
    let y = (parseFloat(style.paddingTop) || 0) + (height - block) / 2;
    // The layout writes the reading order the keys follow, and numbers the
    // cards by it: 1 is the top-left card, whatever slot it sits in.
    state.rows = rows.map((row) => row.members.map(({ i }) => Number(cards[i].dataset.index)));
    state.order = state.rows.flat();
    state.order.forEach((setIndex, k) => {
      const number = k + 1;
      const key = cards.find((card) => Number(card.dataset.index) === setIndex)?.querySelector('kbd');
      if (key) key.textContent = number === 10 ? '0' : number === 11 ? '-' : number === 12 ? '=' : String(number);
    });
    for (const row of rows) {
      let x = (parseFloat(style.paddingLeft) || 0) + (width - row.width) / 2;
      for (const { i, w, h } of row.members) {
        const card = cards[i];
        const cw = Math.round(w);
        const ch = Math.round(h);
        card.style.width = `${cw}px`;
        card.style.height = `${ch}px`;
        card.style.left = `${Math.round(x)}px`;
        card.style.top = `${Math.round(y + (row.tall - h) / 2)}px`;
        x += w + gap;
        const photo = state.set[Number(card.dataset.index)];
        const image = card.querySelector('img');
        const turn = photo.rotate || 0;
        if (turn === 90 || turn === 270) {
          image.style.width = `${ch}px`;
          image.style.height = `${cw}px`;
          image.style.left = `${(cw - ch) / 2}px`;
          image.style.top = `${(ch - cw) / 2}px`;
        } else {
          image.style.width = '';
          image.style.height = '';
          image.style.left = '';
          image.style.top = '';
        }
      }
      y += row.tall + gap;
    }
  }

  function renderSelection() {
    for (const card of stage.querySelectorAll('.rank-card')) {
      card.classList.toggle('is-selected', Number(card.dataset.index) === state.selected);
    }
  }

  function renderProgress() {
    const app = read();
    const shelf = (app.albums || []).find((c) => c.id === app.album);
    const place = app.survey ? `the ${app.survey.length} marked`
      : shelf ? `“${shelf.name.split('/').pop()}”`
        : (app.folders || []).length ? app.folders.map((f) => f.split('/').pop()).join(' + ') : 'your library';
    const narrowed = describe ? describe() : '';
    const where = narrowed ? `${place} · ${narrowed}` : place;
    // Sorted is earned: three rounds settle a place. Seen is one.
    const ranked = state.total
      ? `${state.earned.toLocaleString()} sorted · ${state.judged.toLocaleString()} seen of ${state.total.toLocaleString()} in ${where}`
      : '';
    const sitting = state.rounds ? ` · ${state.rounds} round${state.rounds === 1 ? '' : 's'} this sitting` : '';
    const label = document.querySelector('[data-rank-progress]');
    if (label) label.textContent = ranked + sitting;
    for (const button of document.querySelectorAll('[data-rank-size] button')) {
      const on = Number(button.dataset.size) === state.size;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', String(on));
    }
    for (const button of document.querySelectorAll('[data-rank-mode] button')) {
      const on = button.dataset.mode === state.mode;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', String(on));
    }
  }

  function render() {
    if (!isOpen()) {
      stage.replaceChildren();
      return;
    }
    if (state.set.length < 2) {
      // Three honest states: still asking, truly nothing, or the scope is
      // simply spent for now — in the one empty-state shape the grid and
      // the wall use.
      const said = !state.answered
        ? ['Choosing photographs…', '']
        : state.total === 0
          ? ['Nothing here to rank.', 'Rank works on the photographs you are looking at.']
          : state.judged >= state.total
            ? ['Everything here has been through a round.', 'Change where you are looking, or keep going another sitting.']
            : ['Not enough photographs to rank here yet.', 'They join as their previews are made.'];
      stage.replaceChildren(emptyState({ emptyTitle: said[0], emptyCopy: said[1] }));
      renderProgress();
      return;
    }
    stage.replaceChildren(...state.set.map((photo, index) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'rank-card' + (index === state.selected ? ' is-selected' : '');
      card.dataset.index = index;
      card.dataset.turn = photo.rotate || 0;
      card.setAttribute('aria-label', `Pick ${photo.tail.split('/').pop()}`);
      const image = photo.warm && photo.warm.src === sourceFor(photo) ? photo.warm : document.createElement('img');
      image.alt = '';
      image.decoding = 'async';
      if (!image.src) image.src = sourceFor(photo);
      const number = document.createElement('kbd');
      number.textContent = index + 1 === 10 ? '0' : index + 1 === 11 ? '-' : index + 1 === 12 ? '=' : String(index + 1);
      card.append(image, number);
      return card;
    }));
    layout();
    renderProgress();
    // The keyboard stays on the stage: a rebuilt set focuses its cursor.
    if (document.activeElement === document.body || stage.contains(document.activeElement)) {
      (stage.querySelector('.rank-card.is-selected') || stage).focus({ preventScroll: true });
    }
  }

  stage.addEventListener('click', (event) => {
    const card = event.target.closest('.rank-card');
    if (card) void pick(Number(card.dataset.index), { byMouse: true });
  });
  new ResizeObserver(() => { if (isOpen()) layout(); }).observe(stage);

  return Object.freeze({ open, close, reload: load, resize, remode, pick, key, isOpen, render, size: () => state.size });
}
