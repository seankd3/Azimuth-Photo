// The crop surface: Lightroom's rectangle on Azimuth's loupe. It opens over
// the full-frame picture (the plain loupe, never the cropped rendition),
// shows the rectangle currently worn, and hands are the whole grammar —
// edges and corners resize, the middle moves, Enter applies, Esc leaves
// without writing. The box lives in unit coordinates of the oriented
// image, exactly as the sidecar spells it; the owner's turn is a display
// transform mapped through, same as everywhere else.

const EDGE = 14;          // how close counts as grabbing an edge, in px
const LEAST = 0.02;       // no crop thinner than 2% survives a drag

export function createCropSurface({ product, notify, applied }) {
  const surface = document.querySelector('[data-crop]');
  const picture = surface.querySelector('img');
  const box = surface.querySelector('.crop-box');
  const state = { photo: null, turn: 0, unit: [0, 0, 1, 1], held: null, had: null };

  function isOpen() {
    return !surface.hidden;
  }

  async function open(photo) {
    let held;
    try {
      held = await product.cropState(photo.id);
    } catch (error) {
      notify(error.message);
      return;
    }
    if (!held.plain) {
      notify('The full picture is not here yet — it arrives with its tiles.');
      return;
    }
    state.photo = photo;
    state.turn = photo.rotate || 0;
    state.unit = held.box ? [...held.box] : [0, 0, 1, 1];
    state.had = held.box ? [...held.box] : null;
    picture.src = held.plain;
    surface.hidden = false;
    if (picture.complete && picture.naturalWidth) place();
    else picture.onload = place;
  }

  function close() {
    surface.hidden = true;
    state.photo = null;
    state.held = null;
  }

  function isWhole() {
    return state.unit[0] <= 0.001 && state.unit[1] <= 0.001
      && state.unit[2] >= 0.999 && state.unit[3] >= 0.999;
  }

  async function apply() {
    if (!state.photo) return;
    const whole = isWhole();
    const id = state.photo.id;
    try {
      await product.crop(id, whole ? null : state.unit.map((v) => Math.round(v * 1e6) / 1e6));
    } catch (error) {
      notify(error.message);
      return;
    }
    close();
    notify(whole ? 'Crop removed.' : 'Cropped.');
    await applied(id);
  }

  async function reset() {
    state.unit = [0, 0, 1, 1];
    place();
  }

  // ---- geometry: the oriented image, worn turn and all, on this screen ----

  function frame() {
    // Where the oriented-and-turned picture sits in the surface, in px.
    const area = surface.getBoundingClientRect();
    const sideways = state.turn === 90 || state.turn === 270;
    const w = sideways ? picture.naturalHeight : picture.naturalWidth;
    const h = sideways ? picture.naturalWidth : picture.naturalHeight;
    const scale = Math.min((area.width - 48) / w, (area.height - 88) / h);
    const width = w * scale;
    const height = h * scale;
    return {
      left: area.left + (area.width - width) / 2,
      top: area.top + (area.height - height) / 2 + 16,
      width, height, area,
    };
  }

  function toScreen(unit) {
    // A unit point of the oriented image, through the worn turn (clockwise).
    const [x, y] = unit;
    if (state.turn === 90) return [1 - y, x];
    if (state.turn === 180) return [1 - x, 1 - y];
    if (state.turn === 270) return [y, 1 - x];
    return [x, y];
  }

  function fromScreen(point) {
    const [x, y] = point;
    if (state.turn === 90) return [y, 1 - x];
    if (state.turn === 180) return [1 - x, 1 - y];
    if (state.turn === 270) return [1 - y, x];
    return [x, y];
  }

  function place() {
    const held = frame();
    const sideways = state.turn === 90 || state.turn === 270;
    picture.style.width = `${sideways ? held.height : held.width}px`;
    picture.style.height = `${sideways ? held.width : held.height}px`;
    picture.style.left = `${held.left - held.area.left + held.width / 2}px`;
    picture.style.top = `${held.top - held.area.top + held.height / 2}px`;
    picture.style.transform = `translate(-50%, -50%) rotate(${state.turn}deg)`;
    const [l, t] = toScreen([state.unit[0], state.unit[1]]);
    const [r, b] = toScreen([state.unit[2], state.unit[3]]);
    const x0 = Math.min(l, r) * held.width;
    const y0 = Math.min(t, b) * held.height;
    const x1 = Math.max(l, r) * held.width;
    const y1 = Math.max(t, b) * held.height;
    box.style.left = `${held.left - held.area.left + x0}px`;
    box.style.top = `${held.top - held.area.top + y0}px`;
    box.style.width = `${x1 - x0}px`;
    box.style.height = `${y1 - y0}px`;
  }

  // ---- hands ----

  function grip(event) {
    // Which part of the box the pointer is on: edges, corners, or the middle.
    // A full frame has no edges worth grabbing and nowhere to move — any
    // drag on it draws the first box, which is how a crop begins.
    if (isWhole()) return null;
    const at = box.getBoundingClientRect();
    const x = event.clientX;
    const y = event.clientY;
    const near = (edge, value) => Math.abs(edge - value) <= EDGE;
    const inside = x >= at.left - EDGE && x <= at.right + EDGE
      && y >= at.top - EDGE && y <= at.bottom + EDGE;
    if (!inside) return null;
    return {
      left: near(at.left, x), right: near(at.right, x),
      top: near(at.top, y), bottom: near(at.bottom, y),
      move: !near(at.left, x) && !near(at.right, x) && !near(at.top, y) && !near(at.bottom, y),
    };
  }

  surface.addEventListener('pointerdown', (event) => {
    if (event.target.closest('button')) return;
    const on = grip(event);
    state.held = {
      id: event.pointerId, x: event.clientX, y: event.clientY,
      unit: [...state.unit],
      on: on || { left: false, right: false, top: false, bottom: false, move: false, draw: true },
    };
    if (state.held.on.draw) {
      // A drag on the picture outside the box draws a fresh one from here.
      const held = frame();
      const sx = (event.clientX - held.left) / held.width;
      const sy = (event.clientY - held.top) / held.height;
      const [ux, uy] = fromScreen([Math.min(1, Math.max(0, sx)), Math.min(1, Math.max(0, sy))]);
      state.held.from = [ux, uy];
    }
    try {
      surface.setPointerCapture(event.pointerId);
    } catch { /* synthetic pointers have no capturable id */ }
    event.preventDefault();
  });

  surface.addEventListener('pointermove', (event) => {
    const held = state.held;
    if (!held || event.pointerId !== held.id) return;
    const at = frame();
    const dx = (event.clientX - held.x) / at.width;
    const dy = (event.clientY - held.y) / at.height;
    const [ax, ay] = fromScreen([0, 0]);
    const [bx, by] = fromScreen([dx + 0, dy + 0]);
    // The drag, expressed in oriented units — the turn rotates the delta.
    const ux = bx - ax;
    const uy = by - ay;
    let [l, t, r, b] = held.unit;
    const on = held.on;
    if (on.draw) {
      const sx = (event.clientX - at.left) / at.width;
      const sy = (event.clientY - at.top) / at.height;
      const [cx, cy] = fromScreen([Math.min(1, Math.max(0, sx)), Math.min(1, Math.max(0, sy))]);
      l = Math.min(held.from[0], cx); r = Math.max(held.from[0], cx);
      t = Math.min(held.from[1], cy); b = Math.max(held.from[1], cy);
    } else if (on.move) {
      const w = r - l;
      const h = b - t;
      l = Math.min(1 - w, Math.max(0, l + ux));
      t = Math.min(1 - h, Math.max(0, t + uy));
      r = l + w;
      b = t + h;
    } else {
      // The screen edge being pulled maps to whichever oriented edge the
      // turn says it is; pulling one never crosses its opposite.
      const edges = screenToOriented(on);
      if (edges.left) l = Math.min(r - LEAST, Math.max(0, held.unit[0] + ux));
      if (edges.right) r = Math.max(l + LEAST, Math.min(1, held.unit[2] + ux));
      if (edges.top) t = Math.min(b - LEAST, Math.max(0, held.unit[1] + uy));
      if (edges.bottom) b = Math.max(t + LEAST, Math.min(1, held.unit[3] + uy));
    }
    state.unit = [l, t, r, b];
    place();
  });

  function screenToOriented(on) {
    // Which oriented edges the grabbed screen edges are, under the turn.
    const turns = { 0: ['left', 'top', 'right', 'bottom'],
                    90: ['bottom', 'left', 'top', 'right'],
                    180: ['right', 'bottom', 'left', 'top'],
                    270: ['top', 'right', 'bottom', 'left'] };
    const [sl, st, sr, sb] = turns[state.turn] || turns[0];
    return {
      left: (on.left && sl === 'left') || (on.top && st === 'left') || (on.right && sr === 'left') || (on.bottom && sb === 'left'),
      right: (on.left && sl === 'right') || (on.top && st === 'right') || (on.right && sr === 'right') || (on.bottom && sb === 'right'),
      top: (on.left && sl === 'top') || (on.top && st === 'top') || (on.right && sr === 'top') || (on.bottom && sb === 'top'),
      bottom: (on.left && sl === 'bottom') || (on.top && st === 'bottom') || (on.right && sr === 'bottom') || (on.bottom && sb === 'bottom'),
    };
  }

  surface.addEventListener('pointerup', () => { state.held = null; });
  surface.addEventListener('pointercancel', () => { state.held = null; });
  surface.querySelector('[data-action="crop-apply"]').addEventListener('click', () => void apply());
  surface.querySelector('[data-action="crop-reset"]').addEventListener('click', () => void reset());
  surface.querySelector('[data-action="crop-cancel"]').addEventListener('click', close);
  new ResizeObserver(() => { if (isOpen()) place(); }).observe(surface);

  function key(event) {
    if (event.key === 'Escape') { close(); return true; }
    if (event.key === 'Enter') { void apply(); return true; }
    return false;
  }

  return Object.freeze({ open, close, apply, isOpen, key });
}
