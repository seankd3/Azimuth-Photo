// What an act costs, measured while it happens. A key or a click is meant
// to be answered in the same breath: the document changes, the next frame
// carries the answer, and nothing lurches. This watches every key and every
// click and keeps the last forty, so when something feels wrong the numbers
// for that moment are already in hand -- how long until the first change,
// how long until the paint, how much of the document was rebuilt for it,
// what started moving, and what the browser was busy with instead.
//
// It is off unless asked for: `localStorage['azimuth.friction'] = '1'` in
// the window, or `?friction` on the harness page. Off, nothing is observed,
// nothing is wrapped, and no listener is added.

const KEPT = 40;       // the last forty acts -- a sitting's worth of feel
const WINDOW = 100;    // an act is credited with what happens in its first tenth of a second

function asked() {
  try {
    if (localStorage.getItem('azimuth.friction') === '1') return true;
  } catch {
    // A window with no storage is simply not asked this way.
  }
  return new URLSearchParams(location.search).has('friction');
}

// What the act was, in words the app already uses: the chord, or the
// control's own verb, its label, else the tag it landed on.
function named(event) {
  if (event.type === 'keydown') {
    const chord = (event.ctrlKey || event.metaKey ? 'Ctrl+' : '') + (event.shiftKey ? 'Shift+' : '');
    return chord + (event.key === ' ' ? 'Space' : event.key);
  }
  const on = event.target?.closest?.('[data-action], button, a, [role="button"]') || event.target;
  return on?.dataset?.action || on?.getAttribute?.('aria-label') || on?.tagName?.toLowerCase() || 'click';
}

export function friction() {
  if (!asked()) return null;
  const acts = [];
  // The act being measured. Observers write into this one; an act that
  // overlaps the tenth of a second after another takes the credit from
  // here on, which is the honest reading of two acts that close.
  let open = null;
  const keep = (act) => {
    acts.push(act);
    if (acts.length > KEPT) acts.shift();
    return act;
  };

  function begin(what) {
    const at = performance.now();
    const running = new Set(document.getAnimations());
    const act = keep({ act: what, at: Math.round(at), change: null, paint: null,
      added: 0, removed: 0, animations: 0, long: 0, shift: 0, errors: 0 });
    open = act;
    // Two frames: the first is the one the handler's work lands in, the
    // second only runs once that frame has been painted.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      act.paint = Math.round(performance.now() - at);
    }));
    setTimeout(() => {
      act.animations = document.getAnimations().filter((one) => !running.has(one)).length;
      if (open === act) open = null;
    }, WINDOW);
  }

  new MutationObserver((records) => {
    if (!open) return;
    if (open.change === null) open.change = Math.round(performance.now() - open.at);
    for (const record of records) {
      open.added += record.addedNodes.length;
      open.removed += record.removedNodes.length;
    }
  }).observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });

  // Two measures the browser may not offer; without them an act simply
  // reports none of them rather than failing to be measured at all.
  const measure = (type, count) => {
    try {
      new PerformanceObserver((list) => list.getEntries().forEach(count)).observe({ type, buffered: false });
    } catch {
      // This browser does not report this one.
    }
  };
  measure('longtask', (entry) => { if (open && entry.duration > 50) open.long += 1; });
  measure('layout-shift', (entry) => {
    // A shift the hand asked for (a scroll, a resize) is not a lurch.
    if (open && !entry.hadRecentInput) open.shift = Math.round((open.shift + entry.value) * 1000) / 1000;
  });

  const spoke = console.error;
  console.error = (...said) => {
    if (open) open.errors += 1;
    spoke.apply(console, said);
  };
  window.addEventListener('error', () => { if (open) open.errors += 1; });

  document.addEventListener('keydown', (event) => begin(named(event)), true);
  document.addEventListener('click', (event) => begin(named(event)), true);

  const meter = {
    acts: () => acts.map((act) => ({ ...act })),
    clear: () => { acts.length = 0; },
    // A note in the same stream, so what a person says about a moment sits
    // beside the numbers for it.
    mark: (note) => { keep({ act: `note: ${note}`, at: Math.round(performance.now()) }); },
  };
  window.__friction = meter;
  return meter;
}
