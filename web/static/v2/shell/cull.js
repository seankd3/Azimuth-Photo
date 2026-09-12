// Decisions from the keyboard: one product call, every loaded row the answer
// names edited in place by identity, the selection moved on, and one Undo
// toast holding the exact changes. There is no single/many fork — the reply
// says which identities changed and what to, and the window applies it
// whatever the count. Works the same over the grid and the loupe.
import { numbered } from '../kit/words.js';

export const CULL_MENU = Object.freeze([
  { action: 'pick', label: 'Pick — P' },
  { action: 'clear', label: 'Clear pick — U' },
  { action: 'reject', label: 'Reject — X' },
  { action: 'turnLeft', label: 'Turn left — R' },
  { action: 'turnRight', label: 'Turn right — Shift+R' },
]);

const ACTIONS = Object.freeze({
  pick: { call: (product, ids) => product.pick(ids), message: (n) => n === 1 ? 'Photograph picked.' : `${numbered(n, 'photograph')} picked.`, advance: true },
  clear: { call: (product, ids) => product.clearPick(ids), message: (n) => n === 1 ? 'Pick cleared.' : `${numbered(n, 'pick')} cleared.`, advance: true },
  reject: { call: (product, ids) => product.reject(ids), removes: true, message: (n) => n === 1 ? 'Photograph rejected.' : `${numbered(n, 'photograph')} rejected.` },
  turnRight: { call: (product, ids) => product.turn(ids, 90), message: () => 'Turned right.' },
  turnLeft: { call: (product, ids) => product.turn(ids, 270), message: () => 'Turned left.' },
});

export function createCullWorkflow({ product, read, reload, patch, removed, selection, selectIndex, notify, undo, say = () => {} }) {
  let busy = false;

  // The one path for a decision, from the grid, the loupe or a stage that
  // holds its own rows. A stage passes the ids and a `seat`: how its rows
  // take the change (`patched`), how a row that leaves is replaced
  // (`removed`), and how Undo puts its rows back (`restored`). The library's
  // own rows are patched either way, so the grid behind the stage agrees.
  async function apply(name, { ids: given = null, seat = null } = {}) {
    const action = ACTIONS[name];
    const state = read();
    const ids = given || selection();
    if (!action || busy || !ids.length) return;
    if (!seat && !['library', 'loupe'].includes(state.view)) return;

    busy = true;
    const { selectedIndex } = state;
    try {
      const result = await action.call(product, ids);
      if (!result.changed.length) {
        if (result.unidentified) notify(result.unidentified === 1
          ? 'That photograph is not identified yet — try again in a moment.'
          : `None of these ${result.unidentified.toLocaleString()} are identified yet — try again in a moment.`);
        return;
      }

      if (action.removes) {
        if (seat) await seat.removed(result.changed);
        else await removed(result.changed, selectedIndex);
      } else {
        patch(result.changed);
        if (seat) seat.patched(result.changed);
        // Advancing is the one-at-a-time rhythm; a marked set stays put so
        // the next verb hits the same photographs.
        else if (action.advance && ids.length === 1) await selectIndex(selectedIndex + 1);
      }
      const passed = result.unidentified
        ? ` ${result.unidentified === 1 ? 'One is' : `${result.unidentified} are`} not identified yet.` : '';
      const back = () => product.undoCull(result.changed).then(seat ? seat.restored : reload);
      // One frame flagged or turned shows itself on the tile in the same
      // frame; a toast would say what the eye already saw. Its way back
      // waits on Ctrl+Z. A batch, a reject (the row left) or a word about
      // the unidentified still speaks.
      if (ids.length === 1 && !action.removes && !passed && !seat) { undo.keep(back); say(action.message(1)); }
      else undo.show(action.message(ids.length - result.unidentified) + passed, back);
    } catch (reason) {
      notify(reason.message);
    } finally {
      busy = false;
    }
  }

  return Object.freeze({ apply });
}
