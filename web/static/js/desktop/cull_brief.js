import { getRankings, postJson, thumbUrl, writeFlags } from './api.js';
import { folderValues, scope, scopeParams } from './state.js';
import { showToast } from './toast.js';

const AUTOCULL_BATCH_SIZE = 500;
const SCOPE_PAGE_LIMIT = 5000;

let initialized = false;
let state = emptyState();

function emptyState() {
    return {
        suggestions: [], index: 0, loaded: false, busy: false,
        initialCount: 0, picked: 0, rejected: 0, skipped: 0,
        scopeKind: '', lastAccept: null,
    };
}

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

function host() { return document.getElementById('cull-brief'); }
function banner() { return document.getElementById('cull-brief-banner'); }
function current() { return state.suggestions[state.index] || null; }

function activeScopeKind() {
    if (scope.import_batch) return 'import';
    if (folderValues().length) return 'folder';
    if (scope.date_taken) return 'date';
    return '';
}

function scopeDescription() {
    if (state.scopeKind === 'import') return 'this import';
    if (state.scopeKind === 'folder') return 'this folder';
    if (state.scopeKind === 'date') return 'this date';
    return '';
}

function updateBanner() {
    const el = banner();
    if (!el) return;
    const count = state.suggestions.length;
    el.hidden = count === 0;
    const copy = el.querySelector('span');
    if (copy) {
        copy.textContent = state.scopeKind
            ? `Review ${count.toLocaleString()} scene${count === 1 ? '' : 's'} from ${scopeDescription()}`
            : `${count.toLocaleString()} scenes analyzed · ${count.toLocaleString()} suggested picks`;
    }
}

function focusPoint(member) {
    const box = member?.subject_box || member?.focus_box;
    if (!box || typeof box !== 'object') return { x: 0.5, y: 0.5 };
    const x = Number(box.x ?? box.left);
    const y = Number(box.y ?? box.top);
    const width = Number(box.w ?? box.width);
    const height = Number(box.h ?? box.height);
    if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || x + width > 1 || y + height > 1) {
        return { x: 0.5, y: 0.5 };
    }
    return { x: x + width / 2, y: y + height / 2 };
}

function memberPreview(member) {
    const focus = focusPoint(member);
    return `
        <figure class="cull-brief-member ${member.suggested_pick ? 'suggested' : ''}" data-cull-preview data-focus-x="${focus.x}" data-focus-y="${focus.y}">
            <img src="${esc(thumbUrl('lg', member.id))}" alt="${esc(member.filename)}" decoding="async">
            <figcaption><b>${esc(member.filename)}</b><span>${Math.round(member.quality_score)} quality${member.taste_score != null ? ` · ${Math.round(member.taste_score)} taste` : ''}</span></figcaption>
            ${member.suggested_pick ? '<i>Suggested pick</i>' : ''}
            <span class="cull-brief-zoom-hint">Click to inspect at 100%</span>
        </figure>`;
}

function preloadNext() {
    const next = state.suggestions[state.index + 1] || state.suggestions[0];
    if (!next || next === current()) return;
    for (const member of next.members || []) {
        const image = new Image();
        image.src = thumbUrl('lg', member.id);
    }
}

function renderReview() {
    const el = host();
    if (!el) return;
    const suggestion = current();
    if (!suggestion) return finishReview();
    el.hidden = false;
    const remaining = state.suggestions.length;
    el.innerHTML = `
        <div class="cull-brief-head">
            <div><span class="cull-brief-kicker">Cull brief · ${esc(suggestion.stack_kind)}</span><h2>${remaining} scene${remaining === 1 ? '' : 's'} remaining</h2><p>${state.picked} picked · ${state.rejected} rejected · ${state.skipped} skipped</p></div>
            <button class="icon-btn" type="button" data-cull-close aria-label="Close cull brief">×</button>
        </div>
        <div class="cull-brief-members">${suggestion.members.map(memberPreview).join('')}</div>
        <div class="cull-brief-actions">
            <button class="btn primary" type="button" data-cull-accept ${state.busy ? 'disabled' : ''}>Accept suggestion <kbd>A</kbd></button>
            <button class="btn" type="button" data-cull-skip ${state.busy ? 'disabled' : ''}>Skip <kbd>S</kbd></button>
            <span>${suggestion.member_count} frames · click a preview for its sharpness crop · <kbd>Z</kbd> undo</span>
        </div>`;
    preloadNext();
}

function closeReview() {
    const el = host();
    if (el) el.hidden = true;
}

function finishReview({ undo = null } = {}) {
    closeReview();
    if (!state.initialCount) return;
    showToast(`${state.picked} picked · ${state.rejected} rejected · ${state.skipped} skipped`, { undo });
}

function advance() {
    if (!state.suggestions.length) return finishReview();
    state.index = Math.min(state.index, state.suggestions.length - 1);
    renderReview();
}

function restoreGroups(suggestion) {
    const groups = new Map();
    for (const member of suggestion.members || []) {
        const flag = String(member.flag || 'unflagged');
        groups.set(flag, [...(groups.get(flag) || []), Number(member.id)]);
    }
    return groups;
}

async function undoAccept(receipt = state.lastAccept) {
    if (!receipt || receipt.undone || state.busy) return false;
    state.busy = true;
    try {
        if (!await receipt.commit) return false;
        const results = await Promise.all([...restoreGroups(receipt.suggestion)].map(([flag, ids]) => writeFlags(ids, flag)));
        if (results.some((result) => !result?.ok)) throw new Error('restore failed');
        receipt.undone = true;
        state.lastAccept = null;
        state.picked = Math.max(0, state.picked - 1);
        state.rejected = Math.max(0, state.rejected - Math.max(0, receipt.suggestion.member_count - 1));
        state.suggestions.splice(Math.min(receipt.index, state.suggestions.length), 0, receipt.suggestion);
        state.index = Math.min(receipt.index, state.suggestions.length - 1);
        updateBanner();
        renderReview();
        document.dispatchEvent(new CustomEvent('photoarchive:cull-undone', { detail: receipt }));
        showToast('Cull decision restored');
        return true;
    } catch {
        showToast('Couldn’t undo cull decision');
        return false;
    } finally {
        state.busy = false;
        if (!host()?.hidden) renderReview();
    }
}

async function acceptCurrent() {
    const suggestion = current();
    if (!suggestion || state.busy) return;
    const picked = suggestion.members.find((member) => member.suggested_pick);
    const receipt = { suggestion, index: state.index, undone: false, commit: null, finished: state.suggestions.length === 1 };
    state.lastAccept = receipt;
    state.picked += 1;
    state.rejected += Math.max(0, suggestion.member_count - 1);
    state.suggestions.splice(state.index, 1);
    if (state.index >= state.suggestions.length) state.index = Math.max(0, state.suggestions.length - 1);
    updateBanner();
    if (!state.suggestions.length) {
        finishReview({ undo: () => undoAccept(receipt) });
    } else {
        advance();
        showToast(
            `Picked ${picked?.filename || 'best frame'} · soft-rejected ${Math.max(0, suggestion.member_count - 1)} frame${suggestion.member_count === 2 ? '' : 's'}`,
            { undo: () => undoAccept(receipt) },
        );
    }
    receipt.commit = postJson('/api/quality/autocull/apply', { stack_ids: [suggestion.stack_id] })
        .then((result) => {
            if (!result?.ok) throw new Error(result?.error || 'Couldn’t apply this suggestion');
            document.dispatchEvent(new CustomEvent('photoarchive:cull-applied', { detail: result }));
            return true;
        })
        .catch((error) => {
            if (!receipt.undone) {
                state.picked = Math.max(0, state.picked - 1);
                state.rejected = Math.max(0, state.rejected - Math.max(0, suggestion.member_count - 1));
                const restoreIndex = Math.min(receipt.index, state.suggestions.length);
                state.suggestions.splice(restoreIndex, 0, suggestion);
                state.index = state.suggestions.length === 1
                    ? 0
                    : Math.min(state.suggestions.length - 1, state.index + (restoreIndex <= state.index ? 1 : 0));
                updateBanner();
                if (receipt.finished || !host()?.hidden) renderReview();
            }
            showToast(error.message || 'Couldn’t apply this suggestion');
            return false;
        });
}

function skipCurrent() {
    if (!current() || state.busy) return;
    state.skipped += 1;
    state.suggestions.splice(state.index, 1);
    if (state.index >= state.suggestions.length) state.index = Math.max(0, state.suggestions.length - 1);
    updateBanner();
    advance();
    showToast('Skipped');
}

function togglePreviewZoom(preview) {
    const image = preview.querySelector('img');
    if (!image) return;
    const zoomed = preview.classList.toggle('zoomed');
    if (!zoomed) {
        image.removeAttribute('style');
        return;
    }
    const focusX = Math.min(1, Math.max(0, Number(preview.dataset.focusX) || 0.5));
    const focusY = Math.min(1, Math.max(0, Number(preview.dataset.focusY) || 0.5));
    const position = () => {
        const frame = preview.getBoundingClientRect();
        const width = image.naturalWidth || frame.width;
        const height = image.naturalHeight || frame.height;
        image.style.width = `${width}px`;
        image.style.height = `${height}px`;
        image.style.left = `${frame.width / 2 - width * focusX}px`;
        image.style.top = `${frame.height / 2 - height * focusY}px`;
    };
    if (image.complete) position(); else image.addEventListener('load', position, { once: true });
}

async function scopedSuggestions() {
    const params = scopeParams({ limit: SCOPE_PAGE_LIMIT, offset: 0, stacks: 'collapsed' });
    const page = await getRankings(params);
    const stackIds = [...new Set((page?.images || []).map((image) => Number(image.stack_id)).filter((id) => id > 0))];
    if (!stackIds.length) return { suggestions: [] };
    const suggestions = [];
    for (let index = 0; index < stackIds.length; index += AUTOCULL_BATCH_SIZE) {
        const payload = await postJson('/api/quality/autocull', { stack_ids: stackIds.slice(index, index + AUTOCULL_BATCH_SIZE) });
        suggestions.push(...(payload?.suggestions || []));
    }
    return { suggestions };
}

export async function refreshCullBrief() {
    const scopeKind = activeScopeKind();
    const payload = scopeKind
        ? await scopedSuggestions()
        : await postJson('/api/quality/autocull', { all: true });
    state = {
        ...emptyState(),
        suggestions: payload?.suggestions || [],
        initialCount: (payload?.suggestions || []).length,
        scopeKind,
        loaded: true,
    };
    updateBanner();
    return payload;
}

export function initCullBrief() {
    if (initialized) return;
    initialized = true;
    banner()?.addEventListener('click', (event) => {
        if (!event.target.closest('[data-cull-review]')) return;
        state.index = 0;
        renderReview();
    });
    host()?.addEventListener('click', (event) => {
        if (event.target.closest('[data-cull-close]')) closeReview();
        else if (event.target.closest('[data-cull-accept]')) acceptCurrent();
        else if (event.target.closest('[data-cull-skip]')) skipCurrent();
        else {
            const preview = event.target.closest('[data-cull-preview]');
            if (preview) togglePreviewZoom(preview);
        }
    });
    document.addEventListener('keydown', (event) => {
        if (host()?.hidden || event.metaKey || event.ctrlKey || event.altKey || /input|textarea|select/i.test(document.activeElement?.tagName || '')) return;
        const key = event.key.toLowerCase();
        if (key === 'a') { event.preventDefault(); event.stopImmediatePropagation(); acceptCurrent(); }
        else if (key === 's') { event.preventDefault(); event.stopImmediatePropagation(); skipCurrent(); }
        else if (key === 'z') { event.preventDefault(); event.stopImmediatePropagation(); undoAccept(); }
        else if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); closeReview(); }
    });
    document.addEventListener('photoarchive:import-complete', () => { refreshCullBrief(); });
    setTimeout(() => { refreshCullBrief().catch(() => {}); }, 3000);
}

window.__photoArchiveCullBrief = { init: initCullBrief, refresh: refreshCullBrief };
