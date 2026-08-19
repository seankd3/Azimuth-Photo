// Bringing photographs in: a card that arrives or a folder chosen, staged so
// the person sees every photograph before it enters the library (Lightroom's
// three panes in Azimuth's opinionated form), one kind to confirm, the
// destination shown as truth, one button. Esc before that button writes
// nothing. The work itself is the product's; this is its conversation.

const KIND_LABEL = { raws: 'Camera raws', snapshots: 'Snapshots', edits: 'Edits' };

export function createIntakeWorkflow({ product, notify, afterImport }) {
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
  const state = { source: '', kind: null, roots: {}, candidates: [], checked: new Set(), isCard: false, running: false };
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
    stopButton.hidden = true;
    startButton.hidden = false;
    startButton.textContent = 'Import';
    notify('');
    render();
    if (!dialog.open) dialog.showModal();
  }

  function destinationOf(candidate) {
    const root = state.roots[state.kind] || '…';
    const day = (candidate.taken || '').slice(0, 10);
    return `${root}/${day.slice(0, 4)}/${day}`;
  }

  function render() {
    for (const button of document.querySelectorAll('[data-kind-choice] [data-kind]')) {
      button.classList.toggle('is-active', button.dataset.kind === state.kind);
    }
    document.querySelector('[data-kind-choice]').classList.toggle('is-asking', !state.kind);

    const cells = state.candidates.map((candidate) => {
      const cell = document.createElement('label');
      cell.className = 'stage-cell' + (candidate.suspect ? ' is-suspect' : '') + (state.checked.has(candidate.key) ? ' is-checked' : '');
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
      return cell;
    });
    stage.replaceChildren(...cells);
    for (const image of stage.querySelectorAll('img')) thumbs.observe(image);

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
      await product.bring(state.source, [...state.checked], state.kind, state.isCard && clearBox.checked);
    } catch (error) {
      notify(error.message);
      state.running = false;
      startButton.hidden = false;
      stopButton.hidden = true;
      return;
    }
    poll = setInterval(tick, 1000);
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
      progress.textContent = state.isCard && clearBox.checked && eta !== null
        ? `${done.toLocaleString()} of ${total.toLocaleString()} · ${gb} GB · card free in ~${formatSeconds(eta)}`
        : `${done.toLocaleString()} of ${total.toLocaleString()} · ${gb} GB` + (eta !== null ? ` · ~${formatSeconds(eta)} left` : '');
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
    progress.textContent = (status.phase === 'stopped' ? 'Stopped. ' : status.phase === 'failed' ? `${status.error} ` : '')
      + parts.join(', ') + (cleared ? ' — card empty, safe to eject.' : '.');
    await afterImport();
  }

  function formatSeconds(seconds) {
    if (seconds < 90) return `${seconds}s`;
    return `${Math.round(seconds / 60)} min`;
  }

  function close() {
    if (state.running) return;
    if (dialog.open) dialog.close();
    stage.replaceChildren();
    state.candidates = [];
    state.checked = new Set();
  }

  stage.addEventListener('change', (event) => {
    const key = event.target.dataset.key;
    if (!key) return;
    if (event.target.checked) state.checked.add(key);
    else state.checked.delete(key);
    render();
  });
  document.querySelector('[data-kind-choice]').addEventListener('click', (event) => {
    const kind = event.target.closest('[data-kind]')?.dataset.kind;
    if (!kind) return;
    state.kind = kind;
    render();
  });
  dialog.addEventListener('cancel', (event) => {
    if (state.running) event.preventDefault();
  });
  dialog.addEventListener('close', () => { if (!state.running) close(); });

  return Object.freeze({
    open,
    start,
    stop: () => product.stopIntake().catch(() => {}),
    close,
    checkAll: () => { state.checked = new Set(state.candidates.map((c) => c.key)); render(); },
    checkNew: () => { state.checked = new Set(state.candidates.filter((c) => !c.suspect).map((c) => c.key)); render(); },
    checkNone: () => { state.checked = new Set(); render(); },
    isOpen: () => dialog.open,
    finish: () => { if (startButton.textContent === 'Done') close(); else start(); },
  });
}
