export function createTrashWorkflow({ product, read, reload, status }) {
  const dialog = document.querySelector('[data-empty-dialog]');
  const form = document.querySelector('[data-empty-form]');
  const error = document.querySelector('[data-empty-error]');
  const toast = document.querySelector('[data-toast]');
  let undoChanges = null;

  function closeDialog() {
    if (dialog.open) dialog.close();
    form.reset();
    error.textContent = '';
    form.querySelector('[type="submit"]').disabled = true;
  }

  function hideToast() {
    toast.hidden = true;
    undoChanges = null;
  }

  function showUndo(message, changes) {
    undoChanges = changes;
    toast.querySelector('[data-toast-copy]').textContent = message;
    toast.hidden = false;
  }

  async function restoreSelected() {
    const selected = read().selected;
    if (!selected || read().view !== 'trash') return;
    try {
      const result = await product.restore([selected.id]);
      await reload();
      if (result.changed.length) showUndo('Photograph restored.', result.changed);
    } catch (reason) {
      status.textContent = reason.message;
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
      status.textContent = reason.message;
    }
  }

  async function undo() {
    if (!undoChanges) return;
    const changes = undoChanges;
    hideToast();
    try {
      await product.undoTrash(changes);
      await reload();
    } catch (reason) {
      status.textContent = reason.message;
    }
  }

  form.addEventListener('input', () => {
    form.querySelector('[type="submit"]').disabled =
      form.elements.count.value.trim() !== dialog.dataset.expected;
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
      status.textContent = result.errors.length
        ? `${result.emptied.length.toLocaleString()} deleted; ${result.errors.length.toLocaleString()} could not be deleted.`
        : `${result.emptied.length.toLocaleString()} photographs permanently deleted.`;
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
    undo,
  });
}
