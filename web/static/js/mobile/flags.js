// Optimistic flag writes with undo. The UI updates instantly,
// the write happens in the background, and Undo writes the
// previous flag values back — real writes both ways.

import { writeFlag, writeFlags } from './api.js';
import { byId, emit, on } from './state.js';
import { showToast } from './toast.js';
import { tick } from './haptics.js';

const FLAG_LABELS = {
    picked: 'Favorited',
    rejected: 'Rejected',
    unflagged: 'Flag cleared',
};

function setLocal(ids, flagById) {
    for (const id of ids) {
        const img = byId.get(id);
        const flag = typeof flagById === 'string' ? flagById : flagById.get(id);
        if (img) img.flag = flag;
    }
    emit('flags', { ids, flagOf: (id) => (byId.get(id) || {}).flag || 'unflagged' });
}

async function write(ids, flag) {
    if (ids.length === 1) {
        return await writeFlag(ids[0], flag) !== null;
    }
    return await writeFlags(ids, flag) !== null;
}

async function writeBack(prev) {
    const groups = new Map();
    for (const [id, flag] of prev) {
        if (!groups.has(flag)) groups.set(flag, []);
        groups.get(flag).push(id);
    }
    let ok = true;
    for (const [flag, ids] of groups) {
        if (!(await write(ids, flag))) ok = false;
    }
    return ok;
}

function rollback(ids, prev, appliedFlag) {
    const restore = new Map();
    for (const id of ids) {
        if ((byId.get(id) || {}).flag === appliedFlag) restore.set(id, prev.get(id));
    }
    if (restore.size) setLocal([...restore.keys()], restore);
}

export async function applyFlags(rawIds, flag, { toast = true } = {}) {
    const ids = [...new Set(rawIds.map(Number))].filter((id) => id > 0);
    if (!ids.length) return false;

    const prev = new Map();
    for (const id of ids) {
        prev.set(id, (byId.get(id) || {}).flag || 'unflagged');
    }

    setLocal(ids, flag);
    const outcome = write(ids, flag);
    void outcome.then((ok) => {
        if (!ok) rollback(ids, prev, flag);
    });

    if (toast) {
        const label = ids.length === 1
            ? FLAG_LABELS[flag]
            : `${FLAG_LABELS[flag]} · ${ids.length} photos`;
        if (flag === 'picked' || flag === 'rejected') tick(10);
        showToast(label, {
            undo: async () => {
                setLocal(ids, prev);
                if (!(await writeBack(prev))) {
                    setLocal(ids, flag);
                }
            },
        });
    }
    return true;
}

on('flag-write', ({ ids, status }) => {
    if (status !== 'committed') return;
    emit('flags', { ids, flagOf: (id) => (byId.get(id) || {}).flag || 'unflagged' });
});
