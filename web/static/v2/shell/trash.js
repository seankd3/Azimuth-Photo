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

  async function openDialog() {
    const expected = read().counts.trash;
    if (!expected) return;
    error.textContent = '';
    try {
      await product.emptyTrash(expected, true);
      dialog.querySelector('[data-empty-count]').textContent = expected.toLocaleString();
      dialog.dataset.expected = expected;
      dialog.showModal();
      form.elements.count.focus();
    } catch (reason) {
      notify(why(reason));
    }
  }

  form.addEventListener('input', () => {
    // The number is compared as digits: the dialog printed it with a
    // thousands separator, and typing exactly what it showed must count.
    const typed = form.elements.count.value.replace(/[^0-9]/g, '');
    const right = typed === dialog.dataset.expected;
    form.querySelector('[type="submit"]').disabled = !right;
    error.textContent = typed && !right ? 'That is not the number.' : '';
  });

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
