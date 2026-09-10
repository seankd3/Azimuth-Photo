// Look closer: open the loupe with E, 100% with Z, walk with arrows, a face with ., back with G.
(() => {
  const key = (k) => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  document.querySelectorAll('.photo-cell')[2].click();
  key('e'); key('z'); key('ArrowRight'); key('.');
  return JSON.stringify({ loupe: !document.querySelector('[data-loupe]').hidden, chip: document.querySelector('.loupe-zoom span').textContent });
})()
