// Decisions from the keyboard: one product call, the loaded window edited in
// place by identity, the selection moved on, and one Undo toast holding the
// exact changes. Each action says how it patches a row (or that it removes
// the photograph from this view) and what to call it; nothing else differs.
const ACTIONS = Object.freeze({
  pick: { call: (product, id) => product.pick([id]), patch: () => ({ status: 'picked' }), message: 'Photograph picked.', advance: true },
  clear: { call: (product, id) => product.clearPick([id]), patch: () => ({ status: 'unflagged' }), message: 'Pick cleared.', advance: true },
  reject: { call: (product, id) => product.reject([id]), removes: true, message: 'Photograph rejected.' },
  turnRight: { call: (product, id) => product.turn([id], 90), patch: (photo) => ({ rotate: (photo.rotate + 90) % 360 }), message: 'Turned right.' },
  turnLeft: { call: (product, id) => product.turn([id], 270), patch: (photo) => ({ rotate: (photo.rotate + 270) % 360 }), message: 'Turned left.' },
});

export function createCullWorkflow({ product, read, replace, remove, selectIndex, notify, undo }) {
  let busy = false;

  async function apply(name) {
    const action = ACTIONS[name];
    const state = read();
    if (!action || busy || state.view !== 'library' || !state.selected) return;

    busy = true;
    const { selected, selectedIndex } = state;
    try {
      const result = await action.call(product, selected.id);
      if (!result.changed.length) return;

      if (action.removes) {
        const moved = result.changed.reduce((total, change) => total + change.photos, 0);
        await remove(selectedIndex, moved);
        await selectIndex(selectedIndex);
      } else {
        replace({ ...selected, ...action.patch(selected) });
        if (action.advance) await selectIndex(selectedIndex + 1);
      }
      undo.show(action.message, result.changed);
    } catch (reason) {
      notify(reason.message);
    } finally {
      busy = false;
    }
  }

  return Object.freeze({ apply });
}
