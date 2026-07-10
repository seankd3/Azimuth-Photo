import { writeFlags } from './api.js';
import { byId, emit } from './state.js';
import { showToast } from './toast.js';

function normalizeFlag(flag) {
    return flag === 'picked' || flag === 'rejected' ? flag : 'unflagged';
}

function uniqueIds(images) {
    return [...new Set((images || []).map((image) => Number(image?.id)).filter((id) => id > 0))];
}

function setFlagsLocally(changes) {
    for (const { id, flag } of changes) {
        const image = byId.get(id);
        if (image) image.flag = flag;
    }
    emit('flags', { imageIds: changes.map(({ id }) => id) });
}

async function writeChanges(changes) {
    const grouped = new Map();
    for (const { id, flag } of changes) {
        if (!grouped.has(flag)) grouped.set(flag, []);
        grouped.get(flag).push(id);
    }
    for (const [flag, imageIds] of grouped) {
        const result = await writeFlags(imageIds, flag);
        if (!result || !result.ok) return false;
    }
    return true;
}

export async function keepCoverRejectRest(images, coverId) {
    const imageIds = uniqueIds(images);
    const keeperId = Number(coverId);
    if (!keeperId || !imageIds.includes(keeperId) || imageIds.length < 2) return false;
    const changes = imageIds.map((id) => ({
        id,
        flag: id === keeperId ? 'picked' : 'rejected',
    }));
    const previous = changes.map(({ id }) => ({
        id,
        flag: normalizeFlag(byId.get(id)?.flag),
    }));
    setFlagsLocally(changes);
    if (!await writeChanges(changes)) {
        setFlagsLocally(previous);
        await writeChanges(previous);
        showToast('Couldn’t cull stack');
        return false;
    }
    const rejected = imageIds.length - 1;
    showToast(`Kept cover · Rejected ${rejected} photo${rejected === 1 ? '' : 's'}`, {
        undo: async () => {
            setFlagsLocally(previous);
            const undone = await writeChanges(previous);
            if (!undone) setFlagsLocally(changes);
            showToast(undone ? 'Undone' : 'Couldn’t undo');
        },
    });
    return true;
}
