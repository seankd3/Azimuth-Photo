// The loupe: one photograph, full window, and the two questions a
// photographer asks it — how does it look, and is it sharp. Fit answers the
// first; a click answers the second at 100%, meaning one image pixel to one
// device pixel, which is what "sharp" means on this screen. The wheel zooms
// around the cursor, a drag pans, Esc steps back to Fit before it closes,
// and a turn is part of the same transform — the file is never rewritten.

export function createLoupe({ stage, image, inset = () => 0, onTrouble = () => {} }) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'loupe-zoom';
  chip.title = 'Fit or 100% (Z, Space)';
  const cap = document.createElement('kbd');
  cap.textContent = 'Z';
  const word = document.createElement('span');
  chip.append(cap, word);
  stage.append(chip);

  const state = { scale: 1, fit: 1, full: 1, tx: 0, ty: 0, turn: 0, mode: 'fit', native: null };
  let pointer = null;   // {id, x, y, moved} while a drag may be happening

  function turned() {
    return state.turn === 90 || state.turn === 270;
  }

  function sizes() {
    const width = image.naturalWidth || 1;
    const height = image.naturalHeight || 1;
    return turned() ? { w: height, h: width } : { w: width, h: height };
  }

  function area() {
    // The photograph lives above the filmstrip: the strip is not a curtain
    // over the picture, it takes its slice of the window honestly.
    const box = stage.getBoundingClientRect();
    const pad = inset();
    return { width: box.width, height: box.height - pad,
             cx: box.left + box.width / 2, cy: box.top + (box.height - pad) / 2 };
  }

  function measure() {
    const box = area();
    const { w, h } = sizes();
    // The picture on hand may be a grid tile standing in for the loupe
    // rendition: fit is the photograph's fit, so a stand-in fills the stage
    // as the real picture will, up to the photograph's own pixels.
    const shown = turned() ? image.naturalHeight : image.naturalWidth;
    const ceiling = state.native && shown
      ? Math.max(1, (turned() ? state.native.h : state.native.w) / shown) : 1;
    state.fit = Math.min(box.width / w, box.height / h, ceiling);
    // 100% is one image pixel to one device pixel — the sharpness read.
    state.full = 1 / (window.devicePixelRatio || 1);
  }

  function clamp() {
    const box = area();
    const { w, h } = sizes();
    const overX = Math.max(0, (w * state.scale - box.width) / 2);
    const overY = Math.max(0, (h * state.scale - box.height) / 2);
    state.tx = Math.min(overX, Math.max(-overX, state.tx));
    state.ty = Math.min(overY, Math.max(-overY, state.ty));
  }

  function apply() {
    clamp();
    image.style.transform =
      `translate(-50%, -50%) translate(${state.tx}px, ${state.ty - inset() / 2}px)` +
      ` scale(${state.scale}) rotate(${state.turn}deg)`;
    const zoomed = state.scale > state.fit + 1e-4;
    image.style.cursor = zoomed ? (pointer ? 'grabbing' : 'grab') : 'zoom-in';
    // The chip names what pressing it does, with where you are as a suffix.
    word.textContent = zoomed ? `Fit · ${Math.round(state.scale * (window.devicePixelRatio || 1) * 100)}%` : '100%';
  }

  function toFit() {
    measure();
    state.scale = state.fit;
    state.tx = 0;
    state.ty = 0;
    state.mode = 'fit';
    apply();
  }

  function zoomAt(clientX, clientY, next) {
    const box = area();
    const px = clientX - box.cx;
    const py = clientY - box.cy;
    const grew = next / state.scale;
    state.tx = px - (px - state.tx) * grew;
    state.ty = py - (py - state.ty) * grew;
    state.scale = next;
    state.mode = next <= state.fit + 1e-4 ? 'fit' : 'zoom';
    if (state.mode === 'fit') { state.tx = 0; state.ty = 0; }
    apply();
  }

  function show(photo, source) {
    const fresh = image.dataset.source !== source;
    // The same picture at the same turn has nothing to settle — a background
    // refresh must not snap a pan or a wheel zoom back to centre.
    if (!fresh && (photo.rotate || 0) === state.turn) return;
    state.turn = photo.rotate || 0;
    state.native = photo.width && photo.height ? { w: photo.width, h: photo.height } : null;
    if (fresh) {
      image.dataset.source = source;
      image.src = source;
    }
    const settle = () => {
      measure();
      if (state.mode === 'zoom') {
        // Sharpness runs survive the arrows and a look laid over the
        // picture: stay at 100%, where the eyes were; clamp does the rest.
        state.scale = Math.max(state.full, state.fit);
        apply();
      } else {
        toFit();
      }
    };
    if (image.complete && image.naturalWidth) settle();
    image.onload = settle;
    image.onerror = () => onTrouble();
  }

  function escape() {
    if (state.mode === 'zoom') {
      toFit();
      return true;
    }
    return false;
  }

  function clear() {
    // A neighbour with no picture yet passes through without ending a
    // sharpness run: the pending load is dropped, the mode is kept.
    image.onload = null;
    image.onerror = null;
  }

  function reset() {
    state.mode = 'fit';
    state.turn = 0;
    image.onload = null;
    image.onerror = null;
  }

  // ---- hands ----

  image.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
    try {
      image.setPointerCapture(event.pointerId);
    } catch {
      // a synthetic pointer (tests) has no capturable id; the handlers work anyway
    }
    apply();
    event.preventDefault();
  });
  image.addEventListener('pointermove', (event) => {
    if (!pointer || event.pointerId !== pointer.id) return;
    const dx = event.clientX - pointer.x;
    const dy = event.clientY - pointer.y;
    if (!pointer.moved && Math.hypot(dx, dy) < 4) return;
    pointer.moved = true;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    if (state.mode === 'zoom') {
      state.tx += dx;
      state.ty += dy;
      apply();
    }
  });
  image.addEventListener('pointerup', (event) => {
    if (!pointer || event.pointerId !== pointer.id) return;
    const tapped = !pointer.moved;
    pointer = null;
    if (tapped) {
      // The click asks the sharpness question at this spot — or, already
      // zoomed, steps back to the whole picture.
      if (state.mode === 'zoom') toFit();
      else {
        measure();
        const target = Math.max(state.full, state.fit * 1.0001);
        // A picture already shown at 1:1 or beyond has nothing sharper to
        // reveal; the click changes nothing rather than pretending to.
        if (target > state.fit + 1e-3) zoomAt(event.clientX, event.clientY, target);
      }
    } else {
      apply();
    }
  });
  image.addEventListener('pointercancel', () => { pointer = null; apply(); });

  stage.addEventListener('wheel', (event) => {
    if (stage.hidden) return;
    event.preventDefault();
    const step = event.deltaY < 0 ? 1.18 : 1 / 1.18;
    measure();
    const most = Math.max(state.full, state.fit) * 2;
    const next = Math.min(most, Math.max(state.fit, state.scale * step));
    zoomAt(event.clientX, event.clientY, next);
  }, { passive: false });

  new ResizeObserver(() => { if (!stage.hidden) (state.mode === 'fit' ? toFit : apply)(); }).observe(stage);

  function refresh() {
    if (state.mode === 'fit') toFit();
    else { measure(); apply(); }
  }

  function toggle() {
    // Z, Space, or the chip: the sharpness question at the centre, or the
    // whole picture again.
    if (state.mode === 'zoom') { toFit(); return; }
    measure();
    const box = area();
    const target = Math.max(state.full, state.fit * 1.0001);
    if (target > state.fit + 1e-3) zoomAt(box.cx, box.cy, target);
  }
  chip.addEventListener('click', toggle);

  return Object.freeze({ show, toFit, escape, clear, reset, refresh, toggle });
}
