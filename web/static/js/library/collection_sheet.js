import { escapeHtml } from '../ui.js';


function collectionSheetHtml({ selectedCount }) {
    const noun = selectedCount === 1 ? 'photo' : 'photos';
    return `
        <div class="collection-sheet-backdrop" data-collection-action="close"></div>
        <section class="collection-sheet" role="dialog" aria-modal="true" aria-label="Add to collection">
            <header class="collection-sheet-header">
                <div>
                    <div class="collection-sheet-title">Add to collection</div>
                    <div class="collection-sheet-subtitle">${selectedCount} ${noun} selected</div>
                </div>
                <button type="button" class="collection-sheet-close" data-collection-action="close" aria-label="Close">&times;</button>
            </header>
            <form class="collection-create-row" data-collection-action="create">
                <input name="name" autocomplete="off" placeholder="New collection name" aria-label="New collection name">
                <button type="submit">Create</button>
            </form>
            <div class="collection-sheet-status" data-collection-status>Loading collections...</div>
            <div class="collection-list" data-collection-list></div>
        </section>
    `;
}


function collectionRowHtml(collection) {
    const count = Number(collection.image_count || 0);
    const noun = count === 1 ? 'photo' : 'photos';
    return `
        <button type="button" class="collection-row" data-collection-id="${escapeHtml(collection.id)}">
            <span class="collection-row-cover">${collection.cover_filename ? '' : '+'}</span>
            <span class="collection-row-main">
                <span class="collection-row-name">${escapeHtml(collection.name || 'Untitled collection')}</span>
                <span class="collection-row-meta">${count.toLocaleString()} ${noun} &middot; ${escapeHtml(collection.visibility || 'private')}</span>
            </span>
        </button>
    `;
}


export function createCollectionSheetController({
    documentImpl = document,
    fetchImpl = fetch,
    getSelectedImageIds = () => [],
    clearSelection = () => {},
    showToast = () => {},
} = {}) {
    let host = null;
    let collections = [];
    let submitting = false;

    const selectedIds = () => getSelectedImageIds().map(Number).filter(Boolean);

    function elements() {
        return {
            list: host?.querySelector('[data-collection-list]'),
            status: host?.querySelector('[data-collection-status]'),
            input: host?.querySelector('input[name="name"]'),
        };
    }

    function setStatus(message = '') {
        const status = elements().status;
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('hidden', !message);
    }

    function renderList() {
        const list = elements().list;
        if (!list) return;
        list.innerHTML = collections.length
            ? collections.map(collectionRowHtml).join('')
            : '<div class="collection-empty">No collections yet.</div>';
    }

    async function loadCollections() {
        setStatus('Loading collections...');
        const response = await fetchImpl('/api/user-collections', { cache: 'no-store' });
        if (!response.ok) throw new Error('Collections failed to load');
        const data = await response.json();
        collections = Array.isArray(data.collections) ? data.collections : [];
        setStatus('');
        renderList();
    }

    async function createCollection(name, button = null) {
        const imageIds = selectedIds();
        if (!imageIds.length || submitting) return;
        submitting = true;
        if (button) button.disabled = true;
        try {
            const response = await fetchImpl('/api/user-collections', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, image_ids: imageIds }),
            });
            if (!response.ok) throw new Error('Collection could not be created');
            const data = await response.json();
            showToast(`Added ${imageIds.length} to ${data.collection?.name || name}`);
            clearSelection();
            close();
        } finally {
            submitting = false;
            if (button) button.disabled = false;
        }
    }

    async function addToCollection(collectionId, button = null) {
        const imageIds = selectedIds();
        if (!imageIds.length || submitting) return;
        submitting = true;
        if (button) button.disabled = true;
        try {
            const response = await fetchImpl(`/api/user-collections/${collectionId}/images`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_ids: imageIds }),
            });
            if (!response.ok) throw new Error('Photos could not be added');
            const data = await response.json();
            showToast(`Added ${imageIds.length} to ${data.collection?.name || 'collection'}`);
            clearSelection();
            close();
        } finally {
            submitting = false;
            if (button) button.disabled = false;
        }
    }

    function close() {
        host?.remove();
        host = null;
    }

    async function open() {
        const imageIds = selectedIds();
        if (!imageIds.length) return;
        close();
        host = documentImpl.createElement('div');
        host.className = 'collection-sheet-host';
        host.innerHTML = collectionSheetHtml({ selectedCount: imageIds.length });
        documentImpl.body.appendChild(host);
        elements().input?.focus();
        try {
            await loadCollections();
        } catch (error) {
            setStatus(error?.message || 'Collections failed to load');
        }
    }

    documentImpl.addEventListener('click', (event) => {
        const closeControl = event.target?.closest?.('[data-collection-action="close"]');
        if (closeControl) {
            event.preventDefault();
            close();
            return;
        }
        const row = event.target?.closest?.('[data-collection-id]');
        if (!row || !host?.contains(row)) return;
        event.preventDefault();
        addToCollection(row.dataset.collectionId, row).catch(error => {
            setStatus(error?.message || 'Photos could not be added');
        });
    });

    documentImpl.addEventListener('submit', (event) => {
        const form = event.target?.closest?.('[data-collection-action="create"]');
        if (!form || !host?.contains(form)) return;
        event.preventDefault();
        const input = form.elements.name;
        const name = (input?.value || '').trim();
        if (!name) {
            input?.focus();
            return;
        }
        createCollection(name, form.querySelector('button[type="submit"]')).catch(error => {
            setStatus(error?.message || 'Collection could not be created');
        });
    });

    documentImpl.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && host) close();
    });

    return { open, close };
}
