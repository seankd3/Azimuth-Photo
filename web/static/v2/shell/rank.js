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
// The pick is seen before it leaves. Below this the swap read as the app
// thinking; above it, as waiting.
const HOLD_MS = 120;
// Identities kept out of the next sets, so a frame does not come straight
// back round.
const RECENT = 48;
// Extreme shapes are clamped for sizing only, so one panorama cannot shrink
// every other card in the set.
const ASPECT_MIN = 0.4;
const ASPECT_MAX = 2.6;

export function createRankWorkflow({ product, read, update, notify, undo, onLeave, viewOf }) {
  const stage = document.querySelector('[data-rank]');
  const MODES = ['close', 'random', 'diverse', 'tournament'];
  const MODE_KEY = 'azimuth.rank-mode';
  const state = {
    size: 9, set: [], age: [], buffer: [], recent: [], selected: -1,
    rounds: 0, judged: 0, total: 0, busy: false, filling: null, generation: 0,
    answered: false, queued: null,
    mode: MODES.includes(localStorage.getItem(MODE_KEY)) ? localStorage.getItem(MODE_KEY) : 'close',
  };
  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  function isOpen() {
    return read().view === 'rank';
  }

  function remember(photos) {
    for (const photo of photos) state.recent.push(photo.hash);
    if (state.recent.length > RECENT) state.recent.splice(0, state.recent.length - RECENT);
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
    // the loupe when one exists. A pair on a maximized 4K window is that case.
    const cells = SIZES[state.size] || SIZES[9];
    const wide = (stage.clientWidth / cells[0]) * window.devicePixelRatio > 1024;
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
        notify(error.message);
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
      render();
      void fill();
    } catch (error) {
      notify(error.message);
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
    if (!SIZES[n]) return;
    state.size = n;
    if (isOpen()) await load();
    else render();
  }

  async function remode(mode) {
    if (!MODES.includes(mode) || mode === state.mode) return;
    state.mode = mode;
    localStorage.setItem(MODE_KEY, mode);
    // The buffer was drawn under the old opinion; a fresh deal says the new one.
    state.buffer = [];
    if (isOpen()) await load();
    else render();
  }

  async function pick(index) {
    const winner = state.set[index];
    if (state.busy) {
      // The fastest part of the loop must never eat a keystroke: the pick
      // waits out the beat and lands, instead of vanishing.
      state.queued = index;
      return;
    }
    if (!winner || state.set.length < 2) return;
    state.busy = true;
    const generation = state.generation;
    const losers = state.set.filter((_, i) => i !== index);
    const before = { set: state.set.slice(), age: state.age.slice() };

    // The pick is seen the instant it is made; the write and the hold run
    // underneath it, so the felt beat is the hold — not the hold plus a
    // round-trip.
    const card = stage.querySelector(`[data-index="${index}"]`);
    card?.classList.add('is-picked');
    const held = delay(HOLD_MS);
    let recorded;
    try {
      recorded = await product.round(winner.id, losers.map((p) => p.id));
    } catch (error) {
      card?.classList.remove('is-picked');
      notify(error.message);
      state.busy = false;
      state.queued = null;
      return;
    }
    state.rounds += 1;
    await held;
    if (generation !== state.generation) { state.busy = false; return; }  // left or resized meanwhile

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
      for (const entry of arrivals) {
        if (!entry[1]) entry[1] = state.buffer.shift() || null;
      }
      missing = arrivals.filter(([, next]) => !next).length;
    }
    if (missing) {
      try {
        const answer = await ask(missing);
        if (generation !== state.generation) { state.busy = false; return; }
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
    state.selected = Math.min(index, state.set.length - 1);
    render();
    void fill();
    state.busy = false;

    const over = losers.length;
    undo.show(
      `Picked ${winner.tail.split('/').pop()} over ${over === 1 ? 'one other' : `${over} others`}.`,
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
    const queued = state.queued;
    state.queued = null;
    if (queued !== null) void pick(queued);
  }

  function move(by) {
    if (!state.set.length) return;
    const next = state.selected < 0 ? 0 : Math.min(state.set.length - 1, Math.max(0, state.selected + by));
    state.selected = next;
    renderSelection();
  }

  function key(event) {
    // The keyboard is the fast way through: a digit picks that card, arrows
    // move (or, in a pair, pick a side), Enter picks the selection.
    if (!isOpen()) return false;
    // Digits pick cards one to ten; the two keys past 0 on the same row
    // carry on to eleven and twelve, so every card at twelve-up has a key.
    const digit = event.key === '0' ? 10 : event.key === '-' ? 11 : event.key === '=' ? 12 : Number(event.key);
    if (Number.isInteger(digit) && digit >= 1 && digit <= state.set.length && event.key.length === 1) {
      void pick(digit - 1);
      return true;
    }
    const [cols] = SIZES[state.size];
    if (state.size === 2 && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      void pick(event.key === 'ArrowLeft' ? 0 : 1);
      return true;
    }
    if (event.key === 'ArrowLeft') { move(-1); return true; }
    if (event.key === 'ArrowRight') { move(1); return true; }
    if (event.key === 'ArrowUp') { move(-cols); return true; }
    if (event.key === 'ArrowDown') { move(cols); return true; }
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
    const cards = [...stage.querySelectorAll('.rank-card')];
    if (!cards.length) return;
    const [cols, rows] = SIZES[state.size];
    const style = getComputedStyle(stage);
    const gap = parseFloat(style.gap) || 0;
    const padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    const padY = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const cell = {
      width: (stage.clientWidth - padX - gap * (cols - 1)) / cols,
      height: (stage.clientHeight - padY - gap * (rows - 1)) / rows,
    };
    if (cell.width < 1 || cell.height < 1) return;
    const aspects = cards.map((card) => aspectOf(state.set[Number(card.dataset.index)]));
    const area = Math.min(...aspects.map((ar) => Math.min(cell.height * cell.height * ar, (cell.width * cell.width) / ar)));
    if (!Number.isFinite(area) || area <= 0) return;
    cards.forEach((card, i) => {
      const ar = aspects[i];
      const width = Math.round(Math.sqrt(area * ar));
      const height = Math.round(Math.sqrt(area / ar));
      card.style.width = `${width}px`;
      card.style.height = `${height}px`;
      const photo = state.set[Number(card.dataset.index)];
      const image = card.querySelector('img');
      const turn = photo.rotate || 0;
      if (turn === 90 || turn === 270) {
        image.style.width = `${height}px`;
        image.style.height = `${width}px`;
        image.style.left = `${(width - height) / 2}px`;
        image.style.top = `${(height - width) / 2}px`;
      } else {
        image.style.width = '';
        image.style.height = '';
        image.style.left = '';
        image.style.top = '';
      }
    });
  }

  function renderSelection() {
    for (const card of stage.querySelectorAll('.rank-card')) {
      card.classList.toggle('is-selected', Number(card.dataset.index) === state.selected);
    }
  }

  function renderProgress() {
    const app = read();
    const shelf = (app.albums || []).find((c) => c.id === app.album);
    const where = shelf ? `“${shelf.name.split('/').pop()}”`
      : app.folder ? app.folder.split('/').pop() : 'your library';
    const ranked = state.total
      ? `${state.judged.toLocaleString()} of ${state.total.toLocaleString()} in ${where} ranked`
      : '';
    const sitting = state.rounds ? ` · ${state.rounds} round${state.rounds === 1 ? '' : 's'} this sitting` : '';
    const label = document.querySelector('[data-rank-progress]');
    if (label) label.textContent = ranked + sitting;
    for (const button of document.querySelectorAll('[data-rank-size] button')) {
      button.classList.toggle('is-active', Number(button.dataset.size) === state.size);
    }
    for (const button of document.querySelectorAll('[data-rank-mode] button')) {
      button.classList.toggle('is-active', button.dataset.mode === state.mode);
    }
  }

  function render() {
    if (!isOpen()) {
      stage.replaceChildren();
      return;
    }
    const [cols, rows] = SIZES[state.size];
    stage.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    stage.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
    if (state.set.length < 2) {
      const empty = document.createElement('div');
      empty.className = 'rank-empty';
      // Three honest states: still asking, truly nothing, or the scope is
      // simply spent for now.
      empty.textContent = !state.answered
        ? 'Choosing photographs…'
        : state.total === 0
          ? 'Nothing here to rank.'
          : state.judged >= state.total
            ? 'Everything here has been through a round. Change where you are looking, or keep going another sitting.'
            : 'Not enough photographs to rank here yet — they join as their previews are made.';
      stage.replaceChildren(empty);
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
  }

  stage.addEventListener('click', (event) => {
    const card = event.target.closest('.rank-card');
    if (card) void pick(Number(card.dataset.index));
  });
  new ResizeObserver(() => { if (isOpen()) layout(); }).observe(stage);

  return Object.freeze({ open, close, resize, remode, pick, key, isOpen, render, size: () => state.size });
}
