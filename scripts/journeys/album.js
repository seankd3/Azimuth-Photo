// Make an album and fill it: + New album, name it, drag three frames onto it.
(() => {
  document.querySelector('[data-action="new-album"]').click();
  const inp = document.querySelector('[data-name-input]');
  inp.value = 'Client picks'; inp.dispatchEvent(new Event('input', { bubbles: true }));
  return JSON.stringify({ popover: !document.querySelector('[data-name-pop]').hidden, ok: !document.querySelector('[data-action="name-ok"]').disabled });
})()
