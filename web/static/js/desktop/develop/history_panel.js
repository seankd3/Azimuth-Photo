/**
 * Develop's left-rail snapshots and history.  The host owns rendering and
 * persistence; this module only presents saved states and delegates restores.
 */

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function snapshotName(entry) {
    return String(entry?.label || '').replace(/^snapshot:\s*/i, '').trim() || 'Snapshot';
}

function formatTime(value) {
    const parsed = new Date(value || '');
    if (Number.isNaN(parsed.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function titleFor(entry) {
    const label = snapshotName(entry);
    const time = formatTime(entry?.created_at);
    return time ? `${label} · ${time}` : label;
}

export function mountHistoryPanel(host, api) {
    if (!host || host.dataset.historyMounted === '1') return host.__historyController || null;
    const root = document.createElement('section');
    root.id = 'develop-history';
    root.setAttribute('aria-label', 'Snapshots and edit history');
    root.innerHTML = [
        '<section class="develop-history-section">',
        '  <header class="develop-history-head"><strong>Snapshots</strong><button type="button" data-snapshot-add data-tip="Save the current settings as a named snapshot" aria-label="Save snapshot">+</button></header>',
        '  <div data-snapshot-list class="develop-history-list"></div>',
        '  <p data-snapshot-empty class="develop-history-empty">No snapshots yet.</p>',
        '</section>',
        '<section class="develop-history-section">',
        '  <header class="develop-history-head"><strong>History</strong></header>',
        '  <div data-history-list class="develop-history-list"></div>',
        '  <p data-history-empty class="develop-history-empty">No adjustments yet.</p>',
        '</section>',
    ].join('');
    host.appendChild(root);

    const snapshotsEl = root.querySelector('[data-snapshot-list]');
    const historyEl = root.querySelector('[data-history-list]');
    const snapshotsEmpty = root.querySelector('[data-snapshot-empty]');
    const historyEmpty = root.querySelector('[data-history-empty]');
    let history = [];
    let addPopover = null;

    function closeAddPopover() {
        addPopover?.remove();
        addPopover = null;
    }

    function render() {
        const snapshots = history.filter((entry) => /^snapshot:/i.test(String(entry.label || '')));
        const steps = history.filter((entry) => !/^snapshot:/i.test(String(entry.label || '')));
        snapshotsEmpty.hidden = snapshots.length > 0;
        historyEmpty.hidden = steps.length > 0;
        snapshotsEl.innerHTML = snapshots.map((entry) => (
            `<button type="button" class="develop-history-row" data-history-id="${entry.id}" title="${escapeHtml(titleFor(entry))}">${escapeHtml(snapshotName(entry))}</button>`
        )).join('');
        historyEl.innerHTML = steps.map((entry) => (
            `<button type="button" class="develop-history-row" data-history-id="${entry.id}" title="${escapeHtml(titleFor(entry))}"><span>${escapeHtml(entry.label || 'Develop adjustment')}</span><time>${escapeHtml(formatTime(entry.created_at))}</time></button>`
        )).join('');
    }

    function entryFromEvent(event) {
        const button = event.target.closest('[data-history-id]');
        if (!button) return null;
        return history.find((entry) => Number(entry.id) === Number(button.dataset.historyId)) || null;
    }

    async function saveSnapshot(name) {
        const entry = api.getEntry?.();
        if (!entry) return;
        try {
            const response = await fetch(`/api/develop/${entry.imageId}/snapshots`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ label: name, settings: clone(entry.settings) }),
            });
            if (!response.ok) throw new Error('save failed');
            const saved = await response.json();
            history = [saved, ...history];
            render();
            api.notify?.('Snapshot saved');
        } catch { api.notify?.('Could not save snapshot'); }
    }

    function openAddPopover() {
        closeAddPopover();
        addPopover = document.createElement('form');
        addPopover.className = 'develop-history-popover';
        addPopover.innerHTML = '<label>Name<input type="text" maxlength="160" placeholder="e.g. Warm print" autofocus></label><div><button type="button" data-cancel>Cancel</button><button class="primary" type="submit">Save</button></div>';
        root.appendChild(addPopover);
        const input = addPopover.querySelector('input');
        input.focus();
        addPopover.querySelector('[data-cancel]').addEventListener('click', closeAddPopover);
        addPopover.addEventListener('submit', (event) => {
            event.preventDefault();
            const name = String(input.value || '').trim();
            if (!name) return input.focus();
            closeAddPopover();
            saveSnapshot(name);
        });
    }

    root.querySelector('[data-snapshot-add]').addEventListener('click', openAddPopover);
    root.addEventListener('click', (event) => {
        const entry = entryFromEvent(event);
        if (!entry) return;
        api.restoreSettings?.(clone(entry.settings), /^snapshot:/i.test(String(entry.label || '')) ? `Restore snapshot: ${snapshotName(entry)}` : `Restore: ${entry.label || 'Develop adjustment'}`);
    });
    document.addEventListener('pointerdown', (event) => {
        if (addPopover && !addPopover.contains(event.target) && !event.target.closest('[data-snapshot-add]')) closeAddPopover();
    });

    const controller = {
        root,
        setHistory(nextHistory) {
            history = Array.isArray(nextHistory) ? nextHistory.map((entry) => ({ ...entry, settings: clone(entry.settings) })) : [];
            render();
        },
        async reload() {
            const imageId = api.getImageId?.();
            if (!imageId) return;
            try {
                const response = await fetch(`/api/develop/${imageId}/history`, { headers: { Accept: 'application/json' } });
                if (!response.ok) throw new Error('history failed');
                controller.setHistory(await response.json());
            } catch { /* Retain the last known history if the refresh races an image switch. */ }
        },
    };
    host.dataset.historyMounted = '1';
    host.__historyController = controller;
    render();
    return controller;
}
