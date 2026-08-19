// The one Undo toast. It holds a message and the exact way back -- a thunk
// the caller made from what it just did -- and runs it once. Cull, Trash and
// Refine all put their last act here, so there is one Undo and one key.
export function createUndo({ notify }) {
  const toast = document.querySelector('[data-toast]');
  let revert = null;

  function hide() {
    toast.hidden = true;
    revert = null;
  }

  function show(message, nextRevert) {
    revert = nextRevert;
    toast.querySelector('[data-toast-copy]').textContent = message;
    toast.hidden = false;
  }

  async function run() {
    if (!revert) return;
    const current = revert;
    hide();
    try {
      await current();
    } catch (reason) {
      notify(reason.message);
    }
  }

  return Object.freeze({ hide, run, show, pending: () => revert !== null });
}
