// Bringing photographs in: a card that arrives or a folder chosen, staged so
// the person sees every photograph before it enters the library. Import is
// its own workspace, Lightroom's shape in Azimuth's chrome: the main stage
// shows the source's photographs under their days, the inspector's seat
// holds the kind, the destinations and the one button, and the grid's own
// selection grammar works unchanged. Esc before that button writes nothing.
// The work itself is the product's; this is its conversation — and clicking
// Import ends it: the workspace steps back to the library, the work reports
// to the status line, the outcome arrives as a toast, and Import… reopens
// the details (and Stop) while it runs.

import { why } from '../kit/why.js';
import { recall, remember } from '../kit/remembered.js';
import { title as dayName } from '../kit/days.js';
import { numbered } from '../kit/words.js';

export function createIntakeWorkflow({ product, notify, afterImport, progressed = () => {}, enter, leave, isShown, offer = () => {} }) {
  const title = document.querySelector('[data-import-title]');
  const sourceLine = document.querySelector('[data-import-source]');
  const stage = document.querySelector('[data-import-stage]');
  const destinations = document.querySelector('[data-destinations]');
  const summary = document.querySelector('[data-import-summary]');
  const progress = document.querySelector('[data-import-progress]');
  const modeChoice = document.querySelector('[data-mode-choice]');
  const modeSaid = document.querySelector('[data-mode-said]');
  const culledButton = document.querySelector('[data-action="bring-culled"]');
  const startButton = document.querySelector('[data-action="start-import"]');
  const stopButton = document.querySelector('[data-action="stop-import"]');
  const backButton = document.querySelector('[data-action="close-import"]');
  const state = { source: '', kind: null, roots: {}, candidates: [], checked: new Set(), isCard: false, running: false, rolls: {}, mode: 'copy', culled: [] };
  const moving = () => state.mode === 'move';
  // The grid's own selection grammar, in the stage: click, Ctrl adds,
  // Shift ranges, and a checkbox ticked on a selection answers for all of
  // it — Lightroom's import hands, Azimuth's one grammar.
  let order = [];              // candidate keys in rendered (day-grouped) order
  const cells = new Map();     // key -> { cell, box }: the stage answers by key, never by query
  const dayBoxes = new Map();  // day -> its checkbox
  let wornChecked = new Set(); // what the cells show, so a change touches only the difference
  let wornSelected = new Set();
  let selected = new Set();
  let anchor = null;
  let poll = null;

  const thumbs = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      thumbs.unobserve(entry.target);
      const image = entry.target;
      product.thumb(state.source, image.dataset.key).then((url) => { if (url) image.src = url; }).catch(() => {});
    }
    // The workspace is the scroller now; watching the stage itself would
    // count every cell as visible and read the whole card at once.
  }, { root: document.querySelector('.workspace'), rootMargin: '300px' });

  async function open(source, { isCard = false } = {}) {
    if (state.running) {
      // One import at a time; while it runs the workspace is its detail
      // view, and the way out is Back -- nothing here is cancelled by it.
      backButton.textContent = 'Back';
      backButton.title = 'Back to the library — the import keeps running (Esc)';
      enter();
      return;
    }
    backButton.textContent = 'Cancel';
    backButton.title = 'Cancel — nothing is written (Esc)';
    // Work in progress goes to the status line; the toast is for outcomes.
    progressed(`Looking at ${source}…`);
    // The look at a full card reads thousands of files; the growing count is
    // what says the app is working rather than wedged.
    const looking = setInterval(async () => {
      try {
        const held = await product.intakeStatus();
        if (held.phase === 'staging' && held.seen) {
          progressed(`Looking at ${source} — ${numbered(held.seen, 'photograph')}…`);
        }
      } catch { /* the count is a courtesy; the stage call itself reports */ }
    }, 600);
    let staged;
    try {
      staged = await product.stage(source);
    } catch (error) {
      notify(why(error));
      return;
    } finally {
      clearInterval(looking);
      progressed('');
    }
    state.source = staged.source;
    state.kind = staged.kind || recall('azimuth.import-kind', null);
    state.roots = staged.roots || {};
    // Each roll keeps the proposed name beside the person's own, so an
    // emptied field falls back to the proposal instead of trapping the text.
    state.rolls = Object.fromEntries(Object.entries(staged.rolls || {})
      .map(([group, roll]) => [group, { ...roll, proposed: roll.name }]));
    state.candidates = staged.candidates;
    state.isCard = isCard;
    state.running = false;
    state.checked = new Set(staged.candidates.filter((c) => !c.suspect).map((c) => c.key));
    title.textContent = isCard ? 'Import from card' : 'Import folder';
    sourceLine.textContent = `${staged.source} — ${numbered(staged.candidates.length, 'photograph')}` +
      (staged.receiving ? `, into ${staged.receiving}` : '');
    // Remembered per kind of source: a Move learned on a card must never
    // reach a folder on the working disk.
    state.mode = recall(isCard ? 'azimuth.import-mode.card' : 'azimuth.import-mode.folder', 'copy') === 'move' ? 'move' : 'copy';
    state.culled = [];
    culledButton.hidden = true;
    // Erasing the card is never the default: it is chosen once, then
    // remembered as the person's own answer.
    progress.hidden = true;
    progress.textContent = '';
    stopButton.hidden = true;
    startButton.hidden = false;
    startButton.textContent = 'Import';
    notify('');
    renderStage();
    renderRolls();
    render();
    enter();
    startButton.focus();
  }

  function rollName(group) {
    const roll = state.rolls[group || ''] || {};
    const said = (roll.name || '').trim() || roll.proposed || group || '';
    const clean = said.replace(/[<>:"/\\|?*]+/g, ' ').trim();
    return clean || `Roll ${Object.keys(state.rolls).indexOf(group || '') + 1}`;
  }

  function renderRolls() {
    const box = document.querySelector('[data-rolls]');
    box.hidden = state.kind !== 'film';
    if (box.hidden) return;
    const list = document.querySelector('[data-roll-list]');
    list.replaceChildren(...Object.entries(state.rolls).map(([group, roll]) => {
      const item = document.createElement('li');
      const where = document.createElement('span');
      where.textContent = `${group || '(top)'} · ${roll.count} frames`;
      const name = document.createElement('input');
      name.value = roll.name;
      name.dataset.group = group;
      name.setAttribute('aria-label', `Roll name for ${group || 'the top folder'}`);
      item.append(where, name);
      return item;
    }));
  }

  const dayOf = (candidate) => (candidate.taken || '').slice(0, 10);

  const dayTitle = (day) => dayName(day);

  // The stage is built once per staging; a checkbox tick or a roll name only
  // re-answers the summary side. Rebuilding the cells threw away every
  // thumbnail and the focus of the field being typed in. Photographs sit
  // under their day, newest day first — the day's own checkbox is how you
  // import just the dates you came for.
  function renderStage() {
    const byDay = new Map();
    for (const candidate of state.candidates) {
      const day = dayOf(candidate);
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(candidate);
    }
    const days = [...byDay.keys()].sort((a, b) => (a === '') - (b === '') || b.localeCompare(a));
    order = [];
    cells.clear();
    dayBoxes.clear();
    wornChecked = new Set();
    wornSelected = new Set();
    const rows = [];
    for (const day of days) {
      const members = byDay.get(day);
      const head = document.createElement('label');
      head.className = 'stage-day';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.dataset.day = day;
      dayBoxes.set(day, box);
      const caption = document.createElement('span');
      caption.textContent = dayTitle(day);
      const tally = document.createElement('span');
      tally.className = 'stage-day-tally';
      tally.textContent = members.length.toLocaleString();
      head.append(box, caption, tally);
      rows.push(head);
      for (const candidate of members) {
        order.push(candidate.key);
        const cell = document.createElement('label');
        cell.className = 'stage-cell' + (candidate.suspect ? ' is-suspect' : '') + (state.checked.has(candidate.key) ? ' is-checked' : '');
        cell.dataset.key = candidate.key;
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = state.checked.has(candidate.key);
        check.dataset.key = candidate.key;
        // The stage is one tab stop; the arrows and Space walk its cells.
        check.tabIndex = -1;
        const image = document.createElement('img');
        image.alt = '';
        image.dataset.key = candidate.key;
        image.decoding = 'async';
        const name = document.createElement('span');
        name.className = 'stage-name';
        name.textContent = candidate.name;
        const when = document.createElement('span');
        when.className = 'stage-when';
        when.textContent = (candidate.taken || '').slice(0, 16) + (candidate.suspect ? ' · same name and size as one in the library' : '');
        cell.append(check, image, name, when);
        rows.push(cell);
        cells.set(candidate.key, { cell, box: check });
        if (state.checked.has(candidate.key)) wornChecked.add(candidate.key);
      }
    }
    stage.replaceChildren(...rows);
    for (const image of stage.querySelectorAll('.stage-cell img')) thumbs.observe(image);
    selected = new Set();
    anchor = null;
    syncChecks();
  }

  // Only the cells whose state moved are touched: a day's box on a card
  // of thousands is a few dozen writes, not a walk of the whole stage.
  function wear(worn, now, put) {
    for (const key of worn) if (!now.has(key)) put(key, false);
    for (const key of now) if (!worn.has(key)) put(key, true);
    return new Set(now);
  }

  function syncChecks() {
    wornChecked = wear(wornChecked, state.checked, (key, on) => {
      const held = cells.get(key);
      if (!held) return;
      held.box.checked = on;
      held.cell.classList.toggle('is-checked', on);
    });
    wornSelected = wear(wornSelected, selected, (key, on) => {
      cells.get(key)?.cell.classList.toggle('is-selected', on);
    });
    // A day's own box says what its photographs say: all, none, or some.
    const byDay = new Map();
    for (const candidate of state.candidates) {
      const day = dayOf(candidate);
      if (!byDay.has(day)) byDay.set(day, { held: 0, of: 0 });
      const tally = byDay.get(day);
      tally.of += 1;
      if (state.checked.has(candidate.key)) tally.held += 1;
    }
    for (const [day, box] of dayBoxes) {
      const tally = byDay.get(day) || { held: 0, of: 0 };
      box.checked = tally.held > 0 && tally.held === tally.of;
      box.indeterminate = tally.held > 0 && tally.held < tally.of;
    }
  }

  function render() {
    for (const button of document.querySelectorAll('[data-kind-choice] [data-kind]')) {
      button.classList.toggle('is-active', button.dataset.kind === state.kind);
    }
    document.querySelector('[data-kind-choice]').classList.toggle('is-asking', !state.kind);
    // Copy leaves the originals; Move takes each one only after its copy
    // is verified, so a stopped or failed import never loses a file.
    for (const button of modeChoice.querySelectorAll('[data-mode]')) {
      button.classList.toggle('is-active', button.dataset.mode === state.mode);
      button.setAttribute('aria-pressed', String(button.dataset.mode === state.mode));
    }
    modeSaid.textContent = moving()
      ? `Each file leaves the ${state.isCard ? 'card' : 'source'} once its copy is verified.`
      : 'The originals stay where they are.';

    // One list, two jobs: each day says where it will land *and* wears the
    // checkbox that imports it — Lightroom's date picking without a scroll
    // through two thousand thumbnails.
    const byDay = new Map();
    let bytes = 0;
    for (const candidate of state.candidates) {
      const day = dayOf(candidate);
      if (!byDay.has(day)) byDay.set(day, { held: 0, of: 0 });
      const tally = byDay.get(day);
      tally.of += 1;
      if (state.checked.has(candidate.key)) {
        tally.held += 1;
        bytes += candidate.size;
      }
    }
    // The stage's own day rows are the picker; the panel says the span.
    const dated = [...byDay.keys()].filter(Boolean).sort();
    destinations.textContent = dated.length
      ? `${numbered(dated.length, 'day')} · ${dayTitle(dated[0])}${dated.length > 1 ? ` – ${dayTitle(dated.at(-1))}` : ''}`
      : '';
    // The destination said once, as the rule it is — every row repeating
    // the root was noise wearing a path.
    document.querySelector('[data-import-into]').textContent =
      state.kind ? `into ${state.roots[state.kind]}, by date` : 'Choose what these are first.';
    const skipped = state.candidates.length - state.checked.size;
    summary.textContent = `${numbered(state.checked.size, 'photograph')} (${(bytes / 1e9).toFixed(2)} GB)` +
      (skipped ? ` · ${skipped.toLocaleString()} left out` : '');
    startButton.disabled = !state.kind || state.checked.size === 0 || state.running;
  }

  async function start() {
    return launch([...state.checked], false);
  }

  // The import of these keys; `culled` brings back what was culled
  // before, which the first run leaves out.
  async function launch(keys, culled) {
    if (!state.kind || state.running) return;
    if (!keys.length) {
      progress.hidden = false;
      progress.textContent = 'Nothing is checked.';
      return;
    }
    state.running = true;
    startButton.hidden = true;
    culledButton.hidden = true;
    stopButton.hidden = false;
    progress.hidden = false;
    progress.textContent = 'Starting…';
    try {
      const rolls = Object.fromEntries(Object.entries(state.rolls).map(([group]) => [group, rollName(group)]));
      await product.bring(state.source, keys, state.kind, moving(), '', rolls, culled);
    } catch (error) {
      // The panel's own line carries its own refusal, right beside the
      // button that asked.
      progress.textContent = error.message;
      state.running = false;
      startButton.hidden = false;
      stopButton.hidden = true;
      return;
    }
    poll = setInterval(tick, 1000);
    // The decision is made; the conversation ends itself. The work reports
    // to the status line from here.
    progressed('Importing…');
    close();
  }

  async function tick() {
    let status;
    try {
      status = await product.intakeStatus();
    } catch {
      return;
    }
    const done = status.done || 0;
    const total = status.total || 0;
    const left = total - done;
    const rate = status.elapsed && done ? done / status.elapsed : 0;
    const eta = rate ? Math.round(left / rate) : null;
    const gb = ((status.bytes || 0) / 1e9).toFixed(2);
    if (status.phase === 'bringing') {
      const said = state.isCard && moving() && eta !== null
        ? `${done.toLocaleString()} of ${total.toLocaleString()} · ${gb} GB · card free in ~${formatSeconds(eta)}`
        : `${done.toLocaleString()} of ${total.toLocaleString()} · ${gb} GB` + (eta !== null ? ` · ~${formatSeconds(eta)} left` : '');
      progress.textContent = said;
      progressed(`Importing ${said}`);
      return;
    }
    clearInterval(poll);
    poll = null;
    state.running = false;
    stopButton.hidden = true;
    stopButton.disabled = false;
    startButton.hidden = true;
    backButton.textContent = 'Back';
    backButton.title = 'Back to the library (Esc)';
    const parts = [`${(status.brought || 0).toLocaleString()} imported`];
    if (status.already) parts.push(`${status.already.toLocaleString()} already at their place`);
    if (status.skipped) parts.push(`${status.skipped.toLocaleString()} already in the library`);
    if (status.failed) parts.push(`${status.failed.toLocaleString()} could not be imported`);
    // Culled before: the log remembers a cull by the photograph's identity,
    // so a re-inserted card does not bring back what was thrown away. One
    // click brings them anyway.
    if (status.culled) parts.push(`${status.culled.toLocaleString()} culled before stayed out`);
    state.culled = status.culled_keys || [];
    culledButton.hidden = !state.culled.length;
    culledButton.textContent = `Bring the ${state.culled.length.toLocaleString()} culled before`;
    // The panel has usually closed by now; the offer rides the toast,
    // where the outcome lands anyway.
    if (state.culled.length) {
      offer(`${state.culled.length.toLocaleString()} culled before stayed out.`, () => launch(state.culled, true), 'Bring them anyway');
    }
    const cleared = state.isCard && moving() && (status.cleared || 0) >= total && status.phase === 'done';
    const said = (status.removed ? 'The card was removed. ' : status.phase === 'stopped' ? 'Stopped. ' : status.phase === 'failed' ? `${status.error} ` : '')
      + parts.join(', ') + (cleared ? ' — card empty, safe to eject.' : '.');
    progress.textContent = said;
    progressed('');
    // The outcome reaches the person wherever they are.
    notify(said);
    await afterImport({ brought: status.brought || 0 });
  }

  function formatSeconds(seconds) {
    if (seconds < 90) return `${seconds}s`;
    return `${Math.round(seconds / 60)} min`;
  }

  function close() {
    // Closing is always allowed. It puts the conversation away, never the
    // work: a running import continues, the status line carries it, and
    // its outcome arrives as a toast.
    leave();
    if (state.running) return;
    stage.replaceChildren();
    state.candidates = [];
    state.checked = new Set();
    selected = new Set();
    anchor = null;
  }

  // The day answers for its photographs — the whole date in or out. The
  // same answer whether it is asked in the stage or in the panel's list.
  function setDay(day, on) {
    for (const candidate of state.candidates) {
      if (dayOf(candidate) !== day) continue;
      if (on) state.checked.add(candidate.key);
      else state.checked.delete(candidate.key);
    }
    syncChecks();
    render();
  }

  stage.addEventListener('change', (event) => {
    const day = event.target.dataset.day;
    if (day !== undefined) {
      setDay(day, event.target.checked);
      return;
    }
    const key = event.target.dataset.key;
    if (!key) return;
    // A box ticked on a selection answers for all of it, as Lightroom's
    // import does; outside one it answers for its own photograph.
    const keys = selected.has(key) && selected.size > 1 ? [...selected] : [key];
    for (const held of keys) {
      if (event.target.checked) state.checked.add(held);
      else state.checked.delete(held);
    }
    syncChecks();
    render();
  });
  stage.addEventListener('click', (event) => {
    if (event.target.matches('input[type="checkbox"]')) return;
    const cell = event.target.closest('.stage-cell');
    if (!cell) return;
    // The grid's grammar: click selects, Ctrl adds, Shift ranges.
    event.preventDefault();
    const key = cell.dataset.key;
    const index = order.indexOf(key);
    // The anchor is a key, here and on the keyboard, so a Shift+Arrow
    // after a click extends from the photograph that was clicked.
    if (event.shiftKey && anchor !== null) {
      const from = order.indexOf(anchor);
      selected = new Set(order.slice(Math.min(from, index), Math.max(from, index) + 1));
    } else if (event.ctrlKey || event.metaKey) {
      if (selected.has(key)) selected.delete(key);
      else selected.add(key);
      anchor = key;
    } else {
      selected = new Set([key]);
      anchor = key;
    }
    syncChecks();
  });
  document.querySelector('[data-roll-list]').addEventListener('input', (event) => {
    const group = event.target.dataset.group;
    if (group === undefined) return;
    // The field holds exactly what was typed — clearing it brings the
    // proposed name back at import time, not while typing.
    state.rolls[group].name = event.target.value;
    render();
  });
  modeChoice.addEventListener('click', (event) => {
    const mode = event.target.closest('[data-mode]')?.dataset.mode;
    if (!mode) return;
    state.mode = mode;
    remember(state.isCard ? 'azimuth.import-mode.card' : 'azimuth.import-mode.folder', mode);
    render();
  });
  culledButton.addEventListener('click', () => void launch(state.culled, true));
  document.querySelector('[data-kind-choice]').addEventListener('click', (event) => {
    const kind = event.target.closest('[data-kind]')?.dataset.kind;
    if (!kind) return;
    state.kind = kind;
    remember('azimuth.import-kind', kind);
    renderRolls();
    render();
  });
  return Object.freeze({
    open,
    start,
    stop: () => {
      stopButton.disabled = true;
      progress.textContent = 'Stopping…';
      progressed('Stopping the import…');
      return product.stopIntake().catch(() => {});
    },
    close,
    key: (event) => {
      // The grid's grammar on the stage: arrows move a cursor, Shift
      // extends from the anchor, Ctrl+A selects all, Home and End jump.
      if (!order.length) return false;
      // The stage is a grid whose day rows span it, so a day's first row
      // says nothing about the width: the grid template does.
      const across = Math.max(1, getComputedStyle(stage).gridTemplateColumns.split(' ').length);
      const at = anchor === null ? -1 : order.indexOf([...selected].at(-1) ?? anchor);
      const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -across, ArrowDown: across };
      let next = null;
      if (event.key in moves) next = Math.max(0, Math.min(order.length - 1, (at < 0 ? 0 : at + moves[event.key])));
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = order.length - 1;
      else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        selected = new Set(order);
        syncChecks();
        return true;
      } else return false;
      const key = order[next];
      if (event.shiftKey && anchor !== null) {
        const a = order.indexOf(anchor);
        selected = new Set(order.slice(Math.min(a, next), Math.max(a, next) + 1));
      } else {
        selected = new Set([key]);
        anchor = key;
      }
      syncChecks();
      cells.get(key)?.cell.scrollIntoView({ block: 'nearest' });
      return true;
    },
    checkAll: () => { state.checked = new Set(state.candidates.map((c) => c.key)); syncChecks(); render(); },
    checkNew: () => { state.checked = new Set(state.candidates.filter((c) => !c.suspect).map((c) => c.key)); syncChecks(); render(); },
    checkNone: () => { state.checked = new Set(); syncChecks(); render(); },
    // Space over a selection: in or out together, the grid's toggle.
    toggleSelected: () => {
      if (!selected.size) return;
      const everyIn = [...selected].every((key) => state.checked.has(key));
      for (const key of selected) {
        if (everyIn) state.checked.delete(key);
        else state.checked.add(key);
      }
      syncChecks();
      render();
    },
    isOpen: () => isShown(),
    running: () => state.running,
    finish: () => { if (state.running) return; if (startButton.hidden) close(); else start(); },
  });
}
