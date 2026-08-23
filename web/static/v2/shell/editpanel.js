// The edit panel: Lightroom's Basic panel, in the inspector's seat. D over
// the loupe opens it; every slider speaks a crs key in Lightroom's own
// units. Dragging asks for a look — a preview rendered from the plain
// loupe, nothing written; releasing keeps it as one decision. Double-click
// a slider's name to rest it, Reset to rest them all, D or Done to leave.

const GROUPS = [
  ['Light', [
    ['Exposure2012', 'Exposure', -5, 5, 0.05],
    ['Contrast2012', 'Contrast', -100, 100, 1],
    ['Highlights2012', 'Highlights', -100, 100, 1],
    ['Shadows2012', 'Shadows', -100, 100, 1],
    ['Whites2012', 'Whites', -100, 100, 1],
    ['Blacks2012', 'Blacks', -100, 100, 1],
  ]],
  ['Color', [
    ['Temperature', 'Temp', -100, 100, 1],
    ['Tint', 'Tint', -100, 100, 1],
    ['Vibrance', 'Vibrance', -100, 100, 1],
    ['Saturation', 'Saturation', -100, 100, 1],
  ]],
  ['Presence', [
    ['Texture', 'Texture', -100, 100, 1],
    ['Clarity2012', 'Clarity', -100, 100, 1],
    ['Dehaze', 'Dehaze', -100, 100, 1],
  ]],
];
const KEYS = GROUPS.flatMap(([, rows]) => rows.map(([key]) => key));

export function createEditPanel({ product, notify, preview, applied }) {
  const panel = document.querySelector('[data-edit-panel]');
  const groups = panel.querySelector('[data-edit-groups]');
  const state = { photo: null, pending: {}, timer: null, showing: 0 };

  // ---- the sliders, built once ----

  const inputs = new Map();
  const readouts = new Map();
  for (const [title, rows] of GROUPS) {
    const head = document.createElement('p');
    head.className = 'eyebrow';
    head.textContent = title;
    groups.append(head);
    for (const [key, label, low, high, step] of rows) {
      const row = document.createElement('label');
      row.className = 'edit-row';
      const name = document.createElement('span');
      name.className = 'edit-name';
      name.textContent = label;
      name.title = 'Double-click resets';
      const value = document.createElement('span');
      value.className = 'edit-value';
      const slider = document.createElement('input');
      slider.type = 'range';
      slider.min = low;
      slider.max = high;
      slider.step = step;
      slider.value = 0;
      slider.dataset.key = key;
      row.append(name, value, slider);
      groups.append(row);
      inputs.set(key, slider);
      readouts.set(key, value);
      name.addEventListener('dblclick', () => rest(key));
    }
  }
  const gray = document.createElement('label');
  gray.className = 'edit-row edit-check';
  const grayBox = document.createElement('input');
  grayBox.type = 'checkbox';
  grayBox.dataset.key = 'ConvertToGrayscale';
  gray.append(grayBox, document.createTextNode(' Black & white'));
  groups.append(gray);

  function spell(key, value) {
    const number = Number(value) || 0;
    if (key === 'Exposure2012') return (number > 0 ? '+' : '') + number.toFixed(2);
    return (number > 0 ? '+' : '') + Math.round(number);
  }

  function show(settings) {
    for (const key of KEYS) {
      const value = Number(settings[key]) || 0;
      inputs.get(key).value = value;
      readouts.get(key).textContent = spell(key, value);
    }
    grayBox.checked = settings.ConvertToGrayscale === true;
  }

  function isOpen() {
    return !panel.hidden;
  }

  async function open(photo) {
    let held;
    try {
      held = await product.developState(photo.id);
    } catch (error) {
      notify(error.message);
      return;
    }
    if (!held.plain) {
      notify('The full picture is not here yet — it arrives with its tiles.');
      return;
    }
    state.photo = photo;
    state.pending = {};
    show(held.settings || {});
    panel.hidden = false;
    document.querySelector('[data-inspector-facts]').hidden = true;
    // Warm the held base now, so the first drag pays only the pipeline.
    look();
  }

  function close() {
    panel.hidden = true;
    document.querySelector('[data-inspector-facts]').hidden = false;
    clearTimeout(state.timer);
    state.photo = null;
    state.pending = {};
  }

  // ---- looking and keeping ----

  function look() {
    // One preview at a time; the newest ask wins. The stage's own image
    // takes the look, so zoom and pan stay where they were.
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      if (!state.photo) return;
      const asked = ++state.showing;
      try {
        const uri = await product.developPreview(state.photo.id, state.pending, 1024);
        if (asked === state.showing && state.photo) preview(uri);
      } catch (error) {
        // A look that cannot be made is said once, not swallowed — the
        // silent version hid a real failure for an afternoon.
        if (asked === state.showing) notify(error.message);
      }
    }, 80);
  }

  async function keep(patch) {
    if (!state.photo) return;
    try {
      await product.develop(state.photo.id, patch);
    } catch (error) {
      notify(error.message);
      return;
    }
    await applied(state.photo.id);
  }

  groups.addEventListener('input', (event) => {
    const key = event.target.dataset.key;
    if (!key) return;
    if (event.target.type === 'checkbox') return;
    state.pending[key] = Number(event.target.value);
    readouts.get(key).textContent = spell(key, event.target.value);
    look();
  });
  groups.addEventListener('change', (event) => {
    const key = event.target.dataset.key;
    if (!key) return;
    const value = event.target.type === 'checkbox'
      ? (event.target.checked || null)
      : Number(event.target.value);
    state.pending = {};
    void keep({ [key]: value === 0 ? null : value });
  });

  function rest(key) {
    if (!inputs.has(key)) return;
    inputs.get(key).value = 0;
    readouts.get(key).textContent = spell(key, 0);
    state.pending = {};
    void keep({ [key]: null });
  }

  panel.querySelector('[data-action="edit-reset"]').addEventListener('click', () => {
    for (const key of KEYS) {
      inputs.get(key).value = 0;
      readouts.get(key).textContent = spell(key, 0);
    }
    grayBox.checked = false;
    state.pending = {};
    void keep(Object.fromEntries([...KEYS, 'ConvertToGrayscale'].map((key) => [key, null])));
  });
  panel.querySelector('[data-action="edit-close"]').addEventListener('click', close);

  function key(event) {
    if (event.key === 'Escape') { close(); return true; }
    return false;
  }

  function follows(photo) {
    // The panel rides the arrows: a new photograph under the loupe means
    // its own settings on the sliders; leaving the loupe puts it away.
    if (!isOpen()) return;
    if (!photo) { close(); return; }
    if (state.photo && photo.id !== state.photo.id) void open(photo);
  }

  return Object.freeze({ open, close, isOpen, key, follows });
}
