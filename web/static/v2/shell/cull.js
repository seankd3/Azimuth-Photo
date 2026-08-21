// Decisions from the keyboard: one product call, the loaded window edited in
// place by identity, the selection moved on, and one Undo toast holding the
// exact changes. Each action says how it patches a row (or that it removes
// the photograph from this view) and what to call it; nothing else differs.
const ACTIONS = Object.freeze({
  pick: { call: (product, ids) => product.pick(ids), patch: () => ({ status: 'picked' }), message: (n) => n === 1 ? 'Photograph picked.' : `${n} photographs picked.`, advance: true },
  clear: { call: (product, ids) => product.clearPick(ids), patch: () => ({ status: 'unflagged' }), message: (n) => n === 1 ? 'Pick cleared.' : `${n} picks cleared.`, advance: true },
  reject: { call: (product, ids) => product.reject(ids), removes: true, message: (n) => n === 1 ? 'Photograph rejected.' : `${n} photographs rejected.` },
  turnRight: { call: (product, ids) => product.turn(ids, 90), patch: (photo) => ({ rotate: (photo.rotate + 90) % 360 }), message: () => 'Turned right.' },
  turnLeft: { call: (product, ids) => product.turn(ids, 270), patch: (photo) => ({ rotate: (photo.rotate + 270) % 360 }), message: () => 'Turned left.' },
});

export function createCullWorkflow({ product, read, reload, replace, remove, selection, selectIndex, notify, undo }) {
  let busy = false;

  async function apply(name) {
    const action = ACTIONS[name];
    const state = read();
    const ids = selection();
    if (!action || busy || state.view !== 'library' || !ids.length) return;

    busy = true;
    const { selected, selectedIndex } = state;
    try {
      const result = await action.call(product, ids);
      if (!result.changed.length) return;

      if (ids.length > 1) {
        // Many at once: the library is re-read rather than patched cell by
        // cell, and the selection stands so the next verb hits the same set.
        await reload();
      } else if (action.removes) {
        const moved = result.changed.reduce((total, change) => total + change.photos, 0);
        await remove(selectedIndex, moved);
        await selectIndex(selectedIndex);
      } else {
        replace({ ...selected, ...action.patch(selected) });
        if (action.advance) await selectIndex(selectedIndex + 1);
      }
      undo.show(action.message(ids.length), () => product.undoCull(result.changed).then(reload));
    } catch (reason) {
      notify(reason.message);
    } finally {
      busy = false;
    }
  }

  return Object.freeze({ apply });
}
