export function createUndo({ product, reload, notify }) {
  const toast = document.querySelector('[data-toast]');
  let changes = null;

  function hide() {
    toast.hidden = true;
    changes = null;
  }

  function show(message, nextChanges) {
    changes = nextChanges;
    toast.querySelector('[data-toast-copy]').textContent = message;
    toast.hidden = false;
  }

  async function run() {
    if (!changes) return;
    const current = changes;
    hide();
    try {
      await product.undoCull(current);
      await reload();
    } catch (reason) {
      notify(reason.message);
    }
  }

  return Object.freeze({ hide, run, show });
}
