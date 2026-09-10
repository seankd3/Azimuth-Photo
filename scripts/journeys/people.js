// Introduce someone: the wall, arrows, N to name.
(() => {
  document.querySelector('.people-door')?.click();
  document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
  return JSON.stringify({ wall: !document.querySelector('[data-people-stage]').hidden, focused: document.activeElement?.className });
})()
