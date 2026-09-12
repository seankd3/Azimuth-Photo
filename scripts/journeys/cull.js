// Cull by keyboard alone: mark, pick, reject, turn, stack a burst, undo.
(() => {
  const key = (k, o = {}) => (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...o }));
  document.querySelector('.photo-cell').click();
  key('p'); key('ArrowRight'); key('x'); key('ArrowRight'); key('r');
  key('ArrowRight'); key('ArrowRight', { shiftKey: true }); key('ArrowRight', { shiftKey: true }); key('s');
  return JSON.stringify({ marked: document.querySelectorAll('.photo-cell.is-selected').length, toast: document.querySelector('[data-toast-copy]').textContent });
})()
