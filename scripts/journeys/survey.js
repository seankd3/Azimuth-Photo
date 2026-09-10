// A burst judged as a round of its own: mark four, N, pick with Enter, G back.
(() => {
  const key = (k, o = {}) => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...o }));
  const cells = document.querySelectorAll('.photo-cell');
  cells[0].click(); cells[3].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
  key('n');
  return 'survey';
})()
