// Decisions from the keyboard: one product call, every loaded row the answer
// names edited in place by identity, the selection moved on, and one Undo
// toast holding the exact changes. There is no single/many fork — the reply
// says which identities changed and what to, and the window applies it
// whatever the count. Works the same over the grid and the loupe.
export const CULL_MENU = Object.freeze([
  { action: 'pick', label: 'Pick — P' },
  { action: 'clear', label: 'Clear pick — U' },
  { action: 'reject', label: 'Reject — X' },
  { action: 'turnLeft', label: 'Turn left — R' },
  { action: 'turnRight', label: 'Turn right — Shift+R' },
]);

const ACTIONS = Object.freeze({
  pick: { call: (product, ids) => product.pick(ids), message: (n) => n === 1 ? 'Photograph picked.' : `${n} photographs picked.`, advance: true },
  clear: { call: (product, ids) => product.clearPick(ids), message: (n) => n === 1 ? 'Pick cleared.' : `${n} picks cleared.`, advance: true },
  reject: { call: (product, ids) => product.reject(ids), removes: true, message: (n) => n === 1 ? 'Photograph rejected.' : `${n} photographs rejected.` },
  turnRight: { call: (product, ids) => product.turn(ids, 90), message: () => 'Turned right.' },
  turnLeft: { call: (product, ids) => product.turn(ids, 270), message: () => 'Turned left.' },
});

export function createCullWorkflow({ product, read, reload, patch, removed, selection, selectIndex, notify, undo }) {
  let busy = false;

  async function apply(name) {
    const action = ACTIONS[name];
    const state = read();
    const ids = selection();
    if (!action || busy || !['library', 'loupe'].includes(state.view) || !ids.length) return;

    busy = true;
    const { selectedIndex } = state;
    try {
      const result = await action.call(product, ids);
      if (!result.changed.length) return;

      if (action.removes) {
        await removed(result.changed, selectedIndex);
      } else {
        patch(result.changed);
        // Advancing is the one-at-a-time rhythm; a marked set stays put so
        // the next verb hits the same photographs.
        if (action.advance && ids.length === 1) await selectIndex(selectedIndex + 1);
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
