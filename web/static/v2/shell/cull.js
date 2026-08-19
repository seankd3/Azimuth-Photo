const ACTIONS = Object.freeze({
  pick: { call: 'pick', after: 'picked', message: 'Photograph picked.' },
  clear: { call: 'clearPick', after: 'unflagged', message: 'Pick cleared.' },
  reject: { call: 'reject', after: 'trashed', message: 'Photograph rejected.' },
});

export function createCullWorkflow({ product, read, replace, remove, selectIndex, status, undo }) {
  let busy = false;

  async function apply(name) {
    const action = ACTIONS[name];
    const state = read();
    if (!action || busy || state.view !== 'library' || !state.selected) return;

    busy = true;
    const { selected, selectedIndex } = state;
    try {
      const result = await product[action.call]([selected.id]);
      if (!result.changed.length) return;

      if (action.after === 'trashed') {
        const moved = result.changed.reduce((total, change) => total + change.photos, 0);
        await remove(selectedIndex, moved);
        await selectIndex(selectedIndex);
      } else {
        replace({ ...selected, status: action.after });
        await selectIndex(selectedIndex + 1);
      }
      undo.show(action.message, result.changed);
    } catch (reason) {
      status.textContent = reason.message;
    } finally {
      busy = false;
    }
  }

  return Object.freeze({ apply });
}
