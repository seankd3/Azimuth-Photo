// Where photographs can be dropped, said one way: a list names its rows
// and judges each -- a reason means refused, and the row shows the reason
// while the drag is over it; nothing means the drop is welcome. Only the
// app's own drag (its photograph ids) counts; a file from outside is
// refused with its own reason, so a row never lights up for nothing.

export const IDS = 'text/azimuth-ids';

export function acceptDrops(root, selector, { judge = () => null, drop }) {
  const clear = (row) => { row.classList.remove('is-drop', 'is-refused'); delete row.dataset.why; };
  root.addEventListener('dragover', (event) => {
    const row = event.target.closest(selector);
    if (!row) return;
    event.preventDefault();
    const ours = [...event.dataTransfer.types].includes(IDS);
    const why = ours ? judge(row) : 'Only photographs already in the library can be dropped here';
    if (why) {
      event.dataTransfer.dropEffect = 'none';
      row.classList.add('is-refused');
      row.dataset.why = why;
      return;
    }
    event.dataTransfer.dropEffect = 'copy';
    row.classList.add('is-drop');
  });
  root.addEventListener('dragleave', (event) => {
    const row = event.target.closest(selector);
    // Crossing between a row's own children fires dragleave too.
    if (row && !row.contains(event.relatedTarget)) clear(row);
  });
  root.addEventListener('drop', (event) => {
    const row = event.target.closest(selector);
    if (!row) return;
    event.preventDefault();
    const welcome = row.classList.contains('is-drop');
    clear(row);
    if (!welcome) return;
    let ids = [];
    try { ids = JSON.parse(event.dataTransfer.getData(IDS) || '[]'); } catch { ids = []; }
    if (ids.length) void drop(row, ids);
  });
}
