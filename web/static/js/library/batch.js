import { batchExport as exportSelectedImages } from '../export/actions.js';


export function batchBarHtml(selectedCount) {
    return `
                <span>${selectedCount} selected</span>
                <button class="batch-flag-btn flag-picked" data-batch-action="flag" data-flag="picked">P</button>
                <button class="batch-flag-btn flag-unflagged" data-batch-action="flag" data-flag="unflagged">U</button>
                <button class="batch-flag-btn flag-rejected" data-batch-action="flag" data-flag="rejected">X</button>
                <span class="batch-divider"></span>
                <button data-batch-action="collection">Add to collection</button>
                <button data-batch-action="export" data-format="json">Export JSON</button>
                <button data-batch-action="export" data-format="csv">Export CSV</button>
                <button class="batch-cancel" data-batch-action="clear">✕</button>
            `;
}


export function updateBatchBar(selectedCount, {
    documentImpl = document,
} = {}) {
    let bar = documentImpl.getElementById('batch-bar');
    const container = documentImpl.querySelector('.library-container');
    if (selectedCount > 0) {
        if (!bar) {
            bar = documentImpl.createElement('div');
            bar.id = 'batch-bar';
            bar.className = 'batch-bar';
            documentImpl.body.appendChild(bar);
        }
        bar.innerHTML = batchBarHtml(selectedCount);
        container?.classList.add('batch-bar-active');
    } else {
        bar?.remove();
        container?.classList.remove('batch-bar-active');
    }
}


export function markRankCardsSelectable({
    documentImpl = document,
} = {}) {
    documentImpl.querySelectorAll('.rank-card').forEach(c => c.classList.add('selectable'));
}


export function clearBatchSelection(batchSelected, {
    documentImpl = document,
} = {}) {
    batchSelected.clear();
    documentImpl.querySelectorAll('.rank-card').forEach(c => {
        c.classList.remove('selectable', 'selected');
    });
    return { batchMode: false, lastClickedIndex: -1 };
}


export function toggleBatchMode({
    batchMode = false,
    batchSelected,
    documentImpl = document,
} = {}) {
    if (batchMode) {
        return { action: 'clear' };
    }
    markRankCardsSelectable({ documentImpl });
    return { batchMode: true, updateBatchBar: true };
}


export function handleCardClick(event, img, card, index, {
    batchMode = false,
    batchSelected,
    documentImpl = document,
    images = [],
    lastClickedIndex = -1,
} = {}) {
    if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        toggleSelectedCard(batchSelected, img.id, card);
        if (batchSelected.size === 0) {
            return { action: 'clear' };
        }
        markRankCardsSelectable({ documentImpl });
        return { batchMode: true, lastClickedIndex: index, updateBatchBar: true };
    }

    if (event.shiftKey && lastClickedIndex >= 0) {
        event.preventDefault();
        const cards = documentImpl.querySelectorAll('.rank-card');
        const start = Math.min(lastClickedIndex, index);
        const end = Math.max(lastClickedIndex, index);
        for (let i = start; i <= end; i++) {
            if (i < images.length && i < cards.length) {
                batchSelected.add(images[i].id);
                cards[i].classList.add('selected');
            }
        }
        markRankCardsSelectable({ documentImpl });
        return { batchMode: true, lastClickedIndex, updateBatchBar: true };
    }

    if (batchMode) {
        toggleSelectedCard(batchSelected, img.id, card);
        if (batchSelected.size === 0) {
            return { action: 'clear' };
        }
        markRankCardsSelectable({ documentImpl });
        return { batchMode: true, lastClickedIndex: index, updateBatchBar: true };
    }

    return { action: 'open', lastClickedIndex: index, image: img };
}


function toggleSelectedCard(batchSelected, imageId, card) {
    if (batchSelected.has(imageId)) {
        batchSelected.delete(imageId);
        card.classList.remove('selected');
    } else {
        batchSelected.add(imageId);
        card.classList.add('selected');
    }
}


export async function batchFlag(flag, {
    fetchImpl = fetch,
    imageIds = [],
    images = [],
    updateImageFlagLocal,
    clearBatchSelection,
    showToast,
} = {}) {
    if (!imageIds.length) return false;
    const previousFlags = imageIds.map(id => {
        const img = images.find(item => item.id === id);
        return { id, flag: img?.flag || 'unflagged' };
    });
    imageIds.forEach(id => updateImageFlagLocal?.(id, flag));
    try {
        const res = await fetchImpl('/api/images/flag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_ids: imageIds, flag }),
        });
        if (!res.ok) throw new Error('batch flag failed');
        showToast?.(`${imageIds.length} image${imageIds.length > 1 ? 's' : ''} → ${flag}`);
        clearBatchSelection?.();
        return true;
    } catch {
        previousFlags.forEach(item => updateImageFlagLocal?.(item.id, item.flag));
        showToast?.('Batch flag update failed');
        return false;
    }
}


export function batchExport(format, {
    ...options
} = {}) {
    exportSelectedImages(format, options);
}
