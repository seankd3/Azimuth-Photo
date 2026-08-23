// Bringing photographs in: a card that arrives or a folder chosen, staged so
// the person sees every photograph before it enters the library (Lightroom's
// three panes in Azimuth's opinionated form), one kind to confirm, the
// destination shown as truth, one button. Esc before that button writes
// nothing. The work itself is the product's; this is its conversation —
// and clicking Import ends it: the dialog puts itself away, the work
// reports to the status line, the outcome arrives as a toast, and
// Import… reopens the details (and Stop) while it runs.

const KIND_LABEL = { raws: 'Camera raws', snapshots: 'Snapshots', edits: 'Edits', film: 'Film scans' };

export function createIntakeWorkflow({ product, notify, afterImport, progressed = () => {} }) {
  const dialog = document.querySelector('[data-import-dialog]');
  const title = document.querySelector('[data-import-title]');
  const sourceLine = document.querySelector('[data-import-source]');
  const stage = document.querySelector('[data-import-stage]');
  const destinations = document.querySelector('[data-destinations]');
  const summary = document.querySelector('[data-import-summary]');
  const progress = document.querySelector('[data-import-progress]');
  const clearRow = document.querySelector('[data-clear-row]');
  const clearBox = document.querySelector('[data-clear]');
  const startButton = document.querySelector('[data-action="start-import"]');
  const stopButton = document.querySelector('[data-action="stop-import"]');
  const state = { source: '', kind: null, roots: {}, candidates: [], checked: new Set(), isCard: false, running: false, rolls: {} };
  // The grid's own selection grammar, in the stage: click, Ctrl adds,
  // Shift ranges, and a checkbox ticked on a selection answers for all of
  // it — Lightroom's import hands, Azimuth's one grammar.
  let order = [];              // candidate keys in rendered (day-grouped) order
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
  }, { root: stage, rootMargin: '300px' });

  async function open(source, { isCard = false } = {}) {
    if (state.running) {
      // One import at a time; while it runs the dialog is its detail view.
      if (!dialog.open) dialog.showModal();
      return;
    }
    notify(`Looking at ${source}…`);
    let staged;
    try {
      staged = await product.stage(source);
    } catch (error) {
      notify(error.message);
      return;
    }
    state.source = staged.source;
    state.kind = staged.kind;
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
    sourceLine.textContent = `${staged.source} — ${staged.candidates.length.toLocaleString()} photographs` +
      (staged.receiving ? `, into ${staged.receiving}` : '');
    clearRow.hidden = !isCard;
    clearBox.checked = isCard;
    progress.hidden = true;
    progress.textContent = '';
    stopButton.hidden = true;
    startButton.hidden = false;
    startButton.textContent = 'Import';
    notify('');
    renderStage();
    renderRolls();
    render();
    if (!dialog.open) dialog.showModal();
    startButton.focus();
  }

  function rollName(group) {
    const roll = state.rolls[group || ''] || {};
    return (roll.name || '').trim() || roll.proposed || '?';
  }

  function destinationOf(candidate) {
    // A preview of the rule: root/YYYY/YYYY-MM-DD, and for film the roll; a
    // scan's day is its lab folder's date when the folder carries one.
    const root = state.roots[state.kind] || '…';
    const film = state.kind === 'film';
    const day = ((film && candidate.folder_date) || candidate.taken || '').slice(0, 10);
    const roll = film ? `/${rollName(candidate.group)}` : '';
    return `${root}/${day.slice(0, 4)}/${day}${roll}`;
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

  function dayTitle(day) {
    if (!day) return 'Undated';
    const when = new Date(`${day}T12:00:00`);
    if (Number.isNaN(when.getTime())) return day;
    return when.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  }

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
    const rows = [];
    for (const day of days) {
      const members = byDay.get(day);
      const head = document.createElement('label');
      head.className = 'stage-day';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.dataset.day = day;
      const title = document.createElement('span');
      title.textContent = dayTitle(day);
      const count = document.createElement('span');
      count.className = 'stage-day-count';
      count.textContent = members.length.toLocaleString();
      head.append(box, title, count);
      rows.push(head);
      for (const candidate of members) {
        order.push(candidate.key);
        const cell = document.createElement('label');
        cell.className = 'stage-cell' + (candidate.suspect ? ' is-suspect' : '') + (state.checked.has(candidate.key) ? ' is-checked' : '');
        cell.dataset.key = candidate.key;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = state.checked.has(candidate.key);
        box.dataset.key = candidate.key;
        const image = document.createElement('img');
        image.alt = '';
        image.dataset.key = candidate.key;
        image.decoding = 'async';
        const name = document.createElement('span');
        name.className = 'stage-name';
        name.textContent = candidate.name;
        const when = document.createElement('span');
        when.className = 'stage-when';
        when.textContent = (candidate.taken || '').slice(0, 16) + (candidate.suspect ? ' · already imported?' : '');
        cell.append(box, image, name, when);
        rows.push(cell);
      }
    }
    stage.replaceChildren(...rows);
    for (const image of stage.querySelectorAll('.stage-cell img')) thumbs.observe(image);
    selected = new Set();
    anchor = null;
    syncChecks();
  }

  function syncChecks() {
    for (const box of stage.querySelectorAll('.stage-cell input[type="checkbox"]')) {
      const checked = state.checked.has(box.dataset.key);
      box.checked = checked;
      box.closest('.stage-cell').classList.toggle('is-checked', checked);
    }
    for (const cell of stage.querySelectorAll('.stage-cell')) {
      cell.classList.toggle('is-selected', selected.has(cell.dataset.key));
    }
    // A day's own box says what its photographs say: all, none, or some.
    const byDay = new Map();
    for (const candidate of state.candidates) {
      const day = dayOf(candidate);
      if (!byDay.has(day)) byDay.set(day, { held: 0, of: 0 });
      const tally = byDay.get(day);
      tally.of += 1;
      if (state.checked.has(candidate.key)) tally.held += 1;
    }
    for (const box of stage.querySelectorAll('.stage-day input[type="checkbox"]')) {
      const tally = byDay.get(box.dataset.day) || { held: 0, of: 0 };
      box.checked = tally.held > 0 && tally.held === tally.of;
      box.indeterminate = tally.held > 0 && tally.held < tally.of;
    }
  }

  function render() {
    for (const button of document.querySelectorAll('[data-kind-choice] [data-kind]')) {
      button.classList.toggle('is-active', button.dataset.kind === state.kind);
    }
    document.querySelector('[data-kind-choice]').classList.toggle('is-asking', !state.kind);

    const counts = new Map();
    let bytes = 0;
    for (const candidate of state.candidates) {
      if (!state.checked.has(candidate.key)) continue;
      bytes += candidate.size;
      const where = destinationOf(candidate);
      counts.set(where, (counts.get(where) || 0) + 1);
    }
    destinations.replaceChildren(...[...counts.entries()].sort().map(([where, count]) => {
      const item = document.createElement('li');
      const path = document.createElement('span');
      path.textContent = where;
      const n = document.createElement('span');
      n.textContent = count.toLocaleString();
      item.append(path, n);
      return item;
    }));
    const skipped = state.candidates.length - state.checked.size;
    summary.textContent = `${state.checked.size.toLocaleString()} photographs (${(bytes / 1e9).toFixed(2)} GB)` +
      (skipped ? ` · ${skipped.toLocaleString()} left out` : '');
    startButton.disabled = !state.kind || state.checked.size === 0 || state.running;
  }

  async function start() {
    if (!state.kind || state.running) return;
    state.running = true;
    startButton.hidden = true;
    stopButton.hidden = false;
    progress.hidden = false;
    progress.textContent = 'Starting…';
    try {
      const rolls = Object.fromEntries(Object.entries(state.rolls).map(([group]) => [group, rollName(group)]));
      await product.bring(state.source, [...state.checked], state.kind, state.isCard && clearBox.checked, '', rolls);
    } catch (error) {
      // The dialog's own line carries its own refusal — a message behind the
      // modal backdrop is a message to nobody.
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
      const said = state.isCard && clearBox.checked && eta !== null
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
    startButton.hidden = false;
    startButton.textContent = 'Done';
    startButton.disabled = false;
    const parts = [`${(status.brought || 0).toLocaleString()} imported`];
    if (status.already) parts.push(`${status.already} already there`);
    if (status.skipped) parts.push(`${status.skipped} already in the library`);
    if (status.failed) parts.push(`${status.failed} could not be imported`);
    const cleared = state.isCard && clearBox.checked && (status.cleared || 0) >= total && status.phase === 'done';
    const said = (status.phase === 'stopped' ? 'Stopped. ' : status.phase === 'failed' ? `${status.error} ` : '')
      + parts.join(', ') + (cleared ? ' — card empty, safe to eject.' : '.');
    progress.textContent = said;
    progressed('');
    // The dialog put itself away when the work began; the outcome still
    // reaches the person.
    if (!dialog.open) notify(said);
    await afterImport();
  }

  function formatSeconds(seconds) {
    if (seconds < 90) return `${seconds}s`;
    return `${Math.round(seconds / 60)} min`;
  }

  function close() {
    // Closing is always allowed. It puts the conversation away, never the
    // work: a running import continues, the status line carries it, and
    // its outcome arrives as a toast.
    if (dialog.open) dialog.close();
    if (state.running) return;
    stage.replaceChildren();
    state.candidates = [];
    state.checked = new Set();
    selected = new Set();
    anchor = null;
  }

  stage.addEventListener('change', (event) => {
    const day = event.target.dataset.day;
    if (day !== undefined) {
      // The day answers for its photographs — the whole date in or out.
      for (const candidate of state.candidates) {
        if (dayOf(candidate) !== day) continue;
        if (event.target.checked) state.checked.add(candidate.key);
        else state.checked.delete(candidate.key);
      }
      syncChecks();
      render();
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
    if (event.shiftKey && anchor !== null) {
      const [from, to] = [Math.min(anchor, index), Math.max(anchor, index)];
      selected = new Set(order.slice(from, to + 1));
    } else if (event.ctrlKey || event.metaKey) {
      if (selected.has(key)) selected.delete(key);
      else selected.add(key);
      anchor = index;
    } else {
      selected = new Set([key]);
      anchor = index;
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
  document.querySelector('[data-kind-choice]').addEventListener('click', (event) => {
    const kind = event.target.closest('[data-kind]')?.dataset.kind;
    if (!kind) return;
    state.kind = kind;
    renderRolls();
    render();
  });
  dialog.addEventListener('close', () => close());

  return Object.freeze({
    open,
    start,
    stop: () => product.stopIntake().catch(() => {}),
    close,
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
    isOpen: () => dialog.open,
    running: () => state.running,
    finish: () => { if (startButton.textContent === 'Done') close(); else start(); },
  });
}
