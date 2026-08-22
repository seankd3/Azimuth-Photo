// The loupe: one photograph, full window, and the two questions a
// photographer asks it — how does it look, and is it sharp. Fit answers the
// first; a click answers the second at 100%, meaning one image pixel to one
// device pixel, which is what "sharp" means on this screen. The wheel zooms
// around the cursor, a drag pans, Esc steps back to Fit before it closes,
// and a turn is part of the same transform — the file is never rewritten.

export function createLoupe({ dialog, image }) {
  const chip = document.createElement('span');
  chip.className = 'loupe-zoom';
  dialog.append(chip);

  const state = { scale: 1, fit: 1, full: 1, tx: 0, ty: 0, turn: 0, mode: 'fit' };
  let pointer = null;   // {id, x, y, moved} while a drag may be happening

  function turned() {
    return state.turn === 90 || state.turn === 270;
  }

  function sizes() {
    const width = image.naturalWidth || 1;
    const height = image.naturalHeight || 1;
    return turned() ? { w: height, h: width } : { w: width, h: height };
  }

  function measure() {
    const box = dialog.getBoundingClientRect();
    const { w, h } = sizes();
    state.fit = Math.min(box.width / w, box.height / h, 1);
    // 100% is one image pixel to one device pixel — the sharpness read.
    state.full = 1 / (window.devicePixelRatio || 1);
  }

  function clamp() {
    const box = dialog.getBoundingClientRect();
    const { w, h } = sizes();
    const overX = Math.max(0, (w * state.scale - box.width) / 2);
    const overY = Math.max(0, (h * state.scale - box.height) / 2);
    state.tx = Math.min(overX, Math.max(-overX, state.tx));
    state.ty = Math.min(overY, Math.max(-overY, state.ty));
  }

  function apply() {
    clamp();
    image.style.transform =
      `translate(-50%, -50%) translate(${state.tx}px, ${state.ty}px)` +
      ` scale(${state.scale}) rotate(${state.turn}deg)`;
    const zoomed = state.scale > state.fit + 1e-4;
    image.style.cursor = zoomed ? (pointer ? 'grabbing' : 'grab') : 'zoom-in';
    chip.textContent = zoomed ? `${Math.round(state.scale * (window.devicePixelRatio || 1) * 100)}%` : 'Fit';
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
    const box = dialog.getBoundingClientRect();
    const px = clientX - (box.left + box.width / 2);
    const py = clientY - (box.top + box.height / 2);
    const grew = next / state.scale;
    state.tx = px - (px - state.tx) * grew;
    state.ty = py - (py - state.ty) * grew;
    state.scale = next;
    state.mode = next <= state.fit + 1e-4 ? 'fit' : 'zoom';
    if (state.mode === 'fit') { state.tx = 0; state.ty = 0; }
    apply();
  }

  function show(photo, source) {
    state.turn = photo.rotate || 0;
    const fresh = image.dataset.source !== source;
    if (fresh) {
      image.dataset.source = source;
      image.src = source;
    }
    const settle = () => {
      measure();
      if (state.mode === 'zoom') {
        // Sharpness runs survive the arrows: stay at 100%, centred.
        state.scale = Math.max(state.full, state.fit);
        state.tx = 0;
        state.ty = 0;
        apply();
      } else {
        toFit();
      }
    };
    if (image.complete && image.naturalWidth) settle();
    image.onload = settle;
  }

  function escape() {
    if (state.mode === 'zoom') {
      toFit();
      return true;
    }
    return false;
  }

  function reset() {
    state.mode = 'fit';
    image.onload = null;
  }

  // ---- hands ----

  image.addEventListener('pointerdown', (event) => {
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
      else zoomAt(event.clientX, event.clientY, Math.max(state.full, state.fit * 1.0001));
    } else {
      apply();
    }
  });
  image.addEventListener('pointercancel', () => { pointer = null; apply(); });

  dialog.addEventListener('wheel', (event) => {
    if (!dialog.open) return;
    event.preventDefault();
    const step = event.deltaY < 0 ? 1.18 : 1 / 1.18;
    measure();
    const most = Math.max(state.full, state.fit) * 2;
    const next = Math.min(most, Math.max(state.fit, state.scale * step));
    zoomAt(event.clientX, event.clientY, next);
  }, { passive: false });

  new ResizeObserver(() => { if (dialog.open) (state.mode === 'fit' ? toFit : apply)(); }).observe(dialog);

  return Object.freeze({ show, toFit, escape, reset });
}
