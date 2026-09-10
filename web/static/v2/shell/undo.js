// The one toast. Every transient message in the app lands here — a cull, a
// notice, an error — with, when the act can be taken back, the exact way
// back: a thunk the caller made from what it just did. One surface, always
// visible over any chrome, and it expires by itself. The way back has its
// own clock: a notice that lands a moment after a cull replaces the words,
// not the Undo — Ctrl+Z still reverses the cull until its own eight
// seconds are up, and never a decision the person has stopped thinking
// about.
import { why } from '../kit/why.js';

const SHOWN_MS = 8000;
// A notice with no way back has nothing to wait for.
const NOTICE_MS = 3500;

export function createUndo() {
  const toast = document.querySelector('[data-toast]');
  const button = toast.querySelector('button');
  let revert = null;
  let timer = null;
  let revertTimer = null;

  function forget() {
    clearTimeout(revertTimer);
    revert = null;
    button.hidden = true;
  }

  function hide() {
    clearTimeout(timer);
    toast.hidden = true;
    forget();
  }

  function show(message, nextRevert = null) {
    clearTimeout(timer);
    if (nextRevert) {
      clearTimeout(revertTimer);
      revert = nextRevert;
      revertTimer = setTimeout(forget, SHOWN_MS);
    }
    toast.querySelector('[data-toast-copy]').textContent = message;
    // A notice without a way back leaves the live Undo where it is.
    if (nextRevert) button.hidden = false;
    toast.hidden = false;
    timer = setTimeout(() => { toast.hidden = true; }, nextRevert ? SHOWN_MS : NOTICE_MS);
  }

  // Esc puts the surface away; the way back keeps its own clock, so Ctrl+Z
  // still works for the moment it was promised.
  function dismiss() {
    clearTimeout(timer);
    toast.hidden = true;
  }

  async function run() {
    if (!revert) return;
    const current = revert;
    hide();
    try {
      await current();
    } catch (reason) {
      show(why(reason));
    }
  }

  return Object.freeze({ hide, run, show, dismiss, pending: () => revert !== null, visible: () => !toast.hidden });
}
