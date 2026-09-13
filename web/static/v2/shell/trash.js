import { why } from '../kit/why.js';
import { numbered } from '../kit/words.js';

export function createTrashWorkflow({ product, read, reload, notify, undo, selection }) {
  const dialog = document.querySelector('[data-empty-dialog]');
  const form = document.querySelector('[data-empty-form]');
  const error = document.querySelector('[data-empty-error]');

  function closeDialog() {
    if (dialog.open) dialog.close();
    form.reset();
    error.textContent = '';
    form.querySelector('[type="submit"]').disabled = true;
  }

  async function restoreSelected() {
    // The one answer for what a verb acts on: the marked set, else the
    // focused photograph — the same grammar as every cull verb.
    const ids = selection();
    if (!ids.length || read().view !== 'trash') return;
    try {
      const result = await product.restore(ids);
      await reload();
      const n = result.changed.length;
      if (n) {
        undo.show(n === 1 ? 'Photograph restored.' : `${numbered(n, 'photograph')} restored.`,
          () => product.undoCull(result.changed).then(reload));
      }
    } catch (reason) {
      notify(why(reason));
    }
  }

  // The dialog opens the instant it is asked for, with the count the window
  // already knows; the preflight (every drive attached, every file where it
  // was filed) runs while the person reads, and the button waits on it.
  // It used to run first, reading every trashed file byte for byte, and the
  // dialog took as long to appear (the owner, 09-13: "takes forever").
  function arm() {
    const typed = form.elements.count.value.replace(/[^0-9]/g, '');
    const right = typed === dialog.dataset.expected;
    form.querySelector('[type="submit"]').disabled = !right || dialog.dataset.ready !== '1';
    if (dialog.dataset.ready !== 'no') error.textContent = typed && !right ? 'That is not the number.' : '';
  }

  async function openDialog() {
    const expected = read().counts.trash;
    if (!expected) return;
    error.textContent = '';
    dialog.querySelector('[data-empty-count]').textContent = expected.toLocaleString();
    dialog.dataset.expected = expected;
    dialog.dataset.ready = '';
    dialog.showModal();
    form.elements.count.focus();
    try {
      await product.emptyTrash(expected, true);
      if (!dialog.open || dialog.dataset.expected !== String(expected)) return;
      dialog.dataset.ready = '1';
    } catch (reason) {
      if (!dialog.open) return;
      dialog.dataset.ready = 'no';
      error.textContent = why(reason);
    }
    arm();
  }

  form.addEventListener('input', arm);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const expected = Number(dialog.dataset.expected);
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    error.textContent = '';
    try {
      const result = await product.emptyTrash(expected);
      closeDialog();
      await reload();
      notify(result.errors.length
        ? `${numbered(result.emptied.length, 'photograph')} permanently deleted, ${result.errors.length.toLocaleString()} could not be.`
        : `${numbered(result.emptied.length, 'photograph')} permanently deleted.`);
    } catch (reason) {
      error.textContent = reason.message;
    } finally {
      submit.disabled = form.elements.count.value.trim() !== dialog.dataset.expected;
    }
  });

  return Object.freeze({
    closeDialog,
    isOpen: () => dialog.open,
    openDialog,
    restoreSelected,
  });
}
