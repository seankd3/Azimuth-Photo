import { createCollection, getCollectionSuggestions, thumbUrl } from './api.js';
import { setActiveLens } from './state.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';

const DISMISSED_KEY = 'pa_d_dismissed_suggestions';

let suggestions = null;
let suggestionsLoading = false;
let activeIndex = 0;
let mounted = false;
let refreshCollections = async () => {};
let notifyChange = () => {};
const creatingFingerprints = new Set();

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

export const suggestionFingerprint = (s) => (
    s.fingerprint || `${s.kind || ''}|${s.cover_image_id || ''}|${s.count || 0}`
);

function dismissed() {
    try {
        const values = JSON.parse(localStorage.getItem(DISMISSED_KEY) || '[]');
        return Array.isArray(values) ? values.filter((v) => typeof v === 'string') : [];
    } catch {
        return [];
    }
}

function setDismissed(values) {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(values.slice(-100)));
}

function dismissFingerprint(fp) {
    setDismissed([...dismissed().filter((v) => v !== fp), fp]);
}

function restoreFingerprint(fp) {
    setDismissed(dismissed().filter((v) => v !== fp));
}

export function visibleSuggestions() {
    const gone = new Set(dismissed());
    return (suggestions || []).filter((s) => !gone.has(suggestionFingerprint(s)));
}

function currentSuggestions() {
    const visible = visibleSuggestions();
    if (activeIndex >= visible.length) activeIndex = Math.max(0, visible.length - 1);
    return visible;
}

export async function loadSuggestionsOnce() {
    if (suggestions || suggestionsLoading) return;
    suggestionsLoading = true;
    notifyChange();
    const data = await getCollectionSuggestions();
    suggestions = (data && data.suggestions) || [];
    suggestionsLoading = false;
    notifyChange();
    if (mounted) render();
}

export function suggestionsAreLoading() {
    return suggestionsLoading && suggestions == null;
}

export function openSuggestionsReview() {
    activeIndex = 0;
    setActiveLens('suggestions');
}

export async function createSuggestion(suggestion) {
    const fp = suggestionFingerprint(suggestion);
    if (creatingFingerprints.has(fp)) return false;
    creatingFingerprints.add(fp);
    try {
        dismissFingerprint(fp);
        notifyChange();
        render();
        const result = await createCollection(suggestion.title, suggestion.image_ids || [], suggestion.subtitle || '');
        if (!(result && result.ok)) {
            restoreFingerprint(fp);
            notifyChange();
            render();
            showToast("Couldn't create collection");
            return false;
        }
        await refreshCollections();
        showToast('Collection created', { undo: null });
        notifyChange();
        render();
        return true;
    } finally {
        creatingFingerprints.delete(fp);
        render();
    }
}

export function dismissSuggestion(suggestion) {
    const fp = suggestionFingerprint(suggestion);
    dismissFingerprint(fp);
    notifyChange();
    render();
    showToast('Dismissed', {
        undo: () => {
            restoreFingerprint(fp);
            notifyChange();
            render();
        },
    });
}

function rowHtml(suggestion, index) {
    const active = index === activeIndex;
    return `<button class="suggest-review-row ${active ? 'active' : ''}" data-suggest-index="${index}" type="button">`
        + `<span class="suggest-row-cover">${suggestion.cover_image_id ? `<img src="${esc(thumbUrl('sm', suggestion.cover_image_id))}" alt="">` : icon('sparkles')}</span>`
        + '<span class="suggest-row-copy">'
        + `<b>${esc(suggestion.title)}</b>`
        + `<span>${esc(suggestion.reason || 'Suggested')} · ${fmt(suggestion.count)} photos</span>`
        + '</span></button>';
}

function previewHtml(suggestion) {
    const ids = (suggestion.image_ids || []).slice(0, 72);
    if (!ids.length) return '<div class="suggest-empty">No preview available.</div>';
    return ids.map((id) => (
        `<figure class="suggest-preview-thumb"><img loading="lazy" decoding="async" src="${esc(thumbUrl('sm', id))}" alt=""></figure>`
    )).join('');
}

function render() {
    if (!mounted) return;
    const root = document.getElementById('suggestions-flow');
    if (suggestionsLoading && suggestions == null) {
        root.innerHTML = '<div class="suggest-review"><aside class="suggest-review-rail">'
            + '<div class="skel suggest-review-skel"></div><div class="skel suggest-review-skel"></div>'
            + '</aside><section class="suggest-review-main"><div class="skel suggest-preview-skel"></div></section></div>';
        return;
    }

    const visible = currentSuggestions();
    if (!visible.length) {
        root.innerHTML = '<div class="suggest-review empty"><div>'
            + `<span class="suggest-empty-glyph">${icon('sparkles')}</span>`
            + '<h2>Nothing to review</h2>'
            + '</div></div>';
        return;
    }

    const suggestion = visible[activeIndex];
    const creating = creatingFingerprints.has(suggestionFingerprint(suggestion));
    root.innerHTML = '<div class="suggest-review">'
        + `<aside class="suggest-review-rail">${visible.map(rowHtml).join('')}</aside>`
        + '<section class="suggest-review-main">'
        + '<header class="suggest-review-head">'
        + '<div>'
        + `<h2>${esc(suggestion.title)}</h2>`
        + `<p>${esc(suggestion.reason || 'Suggested')} · ${esc(suggestion.subtitle || `${fmt(suggestion.count)} photos`)}</p>`
        + '</div>'
        + '<div class="suggest-review-actions">'
        + `<button class="btn primary" id="suggest-create" type="button" ${creating ? 'disabled' : ''}>Create collection</button>`
        + '<button class="btn" id="suggest-dismiss" type="button">Dismiss</button>'
        + '</div></header>'
        + `<div class="suggest-preview-grid">${previewHtml(suggestion)}</div>`
        + '</section></div>';

    for (const row of root.querySelectorAll('.suggest-review-row')) {
        row.addEventListener('click', () => {
            activeIndex = Number(row.dataset.suggestIndex || 0);
            render();
        });
    }
    root.querySelector('#suggest-create')?.addEventListener('click', async () => {
        await createSuggestion(suggestion);
        currentSuggestions();
    });
    root.querySelector('#suggest-dismiss')?.addEventListener('click', () => {
        dismissSuggestion(suggestion);
        currentSuggestions();
    });
}

function handleKeydown(event) {
    if (!mounted || event.ctrlKey || event.metaKey || event.altKey) return;
    const visible = currentSuggestions();
    const key = event.key.toLowerCase();
    if (key === 'escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        setActiveLens('grid');
    } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        event.stopImmediatePropagation();
        activeIndex = Math.min(visible.length - 1, activeIndex + 1);
        render();
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopImmediatePropagation();
        activeIndex = Math.max(0, activeIndex - 1);
        render();
    } else if (event.key === 'Enter' && visible[activeIndex]) {
        event.preventDefault();
        event.stopImmediatePropagation();
        createSuggestion(visible[activeIndex]);
    } else if ((event.key === 'Backspace' || key === 'd') && visible[activeIndex]) {
        event.preventDefault();
        event.stopImmediatePropagation();
        dismissSuggestion(visible[activeIndex]);
    }
}

export function mountSuggestions() {
    mounted = true;
    document.getElementById('view-suggestions').classList.add('active');
    loadSuggestionsOnce();
    render();
}

export function unmountSuggestions() {
    mounted = false;
    document.getElementById('view-suggestions').classList.remove('active');
}

export function initSuggestions(options = {}) {
    refreshCollections = options.refreshCollections || refreshCollections;
    notifyChange = options.notifyChange || notifyChange;
    window.addEventListener('keydown', handleKeydown, true);
}
