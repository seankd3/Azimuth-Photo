// A cull pass by keyboard: V shows the unflagged, then the picked, then
// everything. Each pass reloads the grid; the keyboard must still be on a
// photograph once the rows have painted, never on the body.
(async () => {
  const key = (k) => (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  const grid = document.querySelector('[data-grid]');
  const painted = () => new Promise((done) => {
    const look = () => (grid.getAttribute('aria-busy') === 'false' && grid.querySelector('.photo-cell') ? done() : setTimeout(look, 20));
    look();
  });
  const where = () => (document.activeElement === document.body ? 'body' : document.activeElement.className.split(' ')[0]);
  document.querySelector('.photo-cell').click();
  const keyboard = [];
  for (let pass = 0; pass < 3; pass += 1) {
    key('v');
    await painted();
    keyboard.push(where());
  }
  return JSON.stringify({ keyboard, toast: document.querySelector('[data-toast-copy]').textContent });
})()
