/**
 * Develop settings clipboard (§29.3).
 *
 * Keeps a deliberate, browser-local copy of a Develop look.  The server remains
 * the authority for group slicing, so a copied full settings object is sent back
 * with its chosen groups when it is pasted.  That makes the copy a snapshot even
 * if its source photo is subsequently edited.
 */

import { SYNC_GROUPS } from './export_dialog.js';

const CLIPBOARD_KEY = 'azimuth.develop.settings-clipboard.v1';
const LAST_SAVED_KEY = 'azimuth.develop.last-saved-image.v1';

function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function validGroups(groups) {
    const known = new Set(SYNC_GROUPS.map(([id]) => id));
    return [...new Set((groups || []).map(String).filter((group) => known.has(group)))];
}

function storageFor(storage) {
    try {
        storage?.getItem(CLIPBOARD_KEY);
        return storage;
    } catch {
        return null;
    }
}

export class DevelopSettingsClipboard {
    constructor({ storage = window.localStorage } = {}) {
        this.storage = storageFor(storage);
        this.memory = this.readStored();
    }

    readStored() {
        try {
            const value = JSON.parse(this.storage?.getItem(CLIPBOARD_KEY) || 'null');
            if (!value || typeof value.settings !== 'object' || !Number(value.sourceId)) return null;
            const groups = validGroups(value.groups);
            return groups.length ? { sourceId: Number(value.sourceId), groups, settings: clone(value.settings) } : null;
        } catch {
            return null;
        }
    }

    copy({ sourceId, settings, groups }) {
        const selected = validGroups(groups);
        if (!Number(sourceId) || !selected.length || !settings || typeof settings !== 'object') return null;
        this.memory = { sourceId: Number(sourceId), groups: selected, settings: clone(settings) };
        try { this.storage?.setItem(CLIPBOARD_KEY, JSON.stringify(this.memory)); } catch { /* memory copy still works */ }
        return this.read();
    }

    read() {
        return this.memory ? { ...this.memory, groups: [...this.memory.groups], settings: clone(this.memory.settings) } : null;
    }

    markSaved(imageId) {
        const id = Number(imageId);
        if (!id) return;
        try { this.storage?.setItem(LAST_SAVED_KEY, String(id)); } catch { /* current-session paste remains available */ }
        this.lastSaved = id;
    }

    lastSavedOtherThan(imageId) {
        const current = Number(imageId);
        const saved = Number(this.lastSaved || this.storage?.getItem(LAST_SAVED_KEY));
        return saved && saved !== current ? saved : null;
    }
}

function copyDialogHtml() {
    const checks = SYNC_GROUPS.map(([id, label]) => (
        `<label class="develop-sync-check" data-tip="Copy ${label}"><input type="checkbox" data-copy-group="${id}" checked> ${label}</label>`
    )).join('');
    return [
        '<strong>Copy settings</strong>',
        '<p class="develop-sync-hint">Choose the adjustments to carry to another photo.</p>',
        `<div class="develop-sync-groups">${checks}</div>`,
        '<button type="button" class="primary" data-copy-confirm>Copy settings</button>',
    ].join('');
}

/** Open the same compact checkbox idiom as Sync, then persist the snapshot. */
export function openCopyDialog({ button, sourceId, settings, anchoredPopover, closePopover, showToast, clipboard }) {
    if (!Number(sourceId) || !settings) return null;
    const popover = anchoredPopover(button, copyDialogHtml());
    popover.classList.add('develop-copy-dialog');
    popover.querySelector('[data-copy-confirm]')?.addEventListener('click', () => {
        const groups = [...popover.querySelectorAll('[data-copy-group]:checked')].map((input) => input.dataset.copyGroup);
        if (!clipboard.copy({ sourceId, settings, groups })) {
            showToast?.('Choose at least one settings group');
            return;
        }
        closePopover?.();
        showToast?.(`Copied ${groups.length} settings group${groups.length === 1 ? '' : 's'}`);
    });
    return popover;
}

async function postSync(body) {
    const response = await fetch('/api/develop/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(payload?.error || 'Settings could not be applied');
    return payload;
}

export async function pasteClipboard({ clipboard, targetIds, onApplied }) {
    const copied = clipboard.read();
    if (!copied) throw new Error('Copy develop settings first');
    const payload = await postSync({
        source_id: copied.sourceId,
        source_settings: copied.settings,
        target_ids: targetIds,
        groups: copied.groups,
        label: 'Pasted settings',
    });
    onApplied?.(payload);
    return payload;
}

export async function applyPrevious({ sourceId, sourceSettings, targetIds, onApplied }) {
    if (!Number(sourceId) || !sourceSettings || typeof sourceSettings !== 'object') {
        throw new Error('Edit another photo, then save it first');
    }
    const payload = await postSync({
        source_id: Number(sourceId),
        source_settings: sourceSettings,
        target_ids: targetIds,
        full: true,
        label: 'From previous',
    });
    onApplied?.(payload);
    return payload;
}
