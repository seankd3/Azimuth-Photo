// The one toast. Every transient message in the app lands here — a cull, a
// notice, an error — with, when the act can be taken back, the exact way
// back: a thunk the caller made from what it just did. One surface, always
// visible over any chrome, and it expires by itself; an Undo expiring drops
// the revert too, so Ctrl+Z can never reverse a decision the person has
// stopped thinking about.
const SHOWN_MS = 8000;

export function createUndo() {
  const toast = document.querySelector('[data-toast]');
  const button = toast.querySelector('button');
  let revert = null;
  let timer = null;

  function hide() {
    clearTimeout(timer);
    toast.hidden = true;
    revert = null;
  }

  function show(message, nextRevert = null) {
    clearTimeout(timer);
    revert = nextRevert;
    toast.querySelector('[data-toast-copy]').textContent = message;
    button.hidden = !nextRevert;
    toast.hidden = false;
    timer = setTimeout(hide, SHOWN_MS);
  }

  async function run() {
    if (!revert) return;
    const current = revert;
    hide();
    try {
      await current();
    } catch (reason) {
      show(reason.message);
    }
  }

  return Object.freeze({ hide, run, show, pending: () => revert !== null });
}
