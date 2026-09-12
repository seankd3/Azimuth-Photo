// Find one photograph from memory: type, arrow to an offer, Enter.
(() => {
  const box = document.querySelector('[data-search]');
  box.focus(); box.value = 'sep'; box.dispatchEvent(new Event('input', { bubbles: true }));
  return JSON.stringify({ drop: !document.querySelector('.search-drop').hidden, offers: document.querySelectorAll('.drop-row').length });
})()
