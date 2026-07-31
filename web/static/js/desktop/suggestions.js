import { createCollection, getCollectionSuggestions, thumbUrl } from './api.js';
import { setActiveLens, scopeParams } from './state.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { foregroundLayerOpen as registeredForegroundLayerOpen } from './layers.js';

const DISMISSED_KEY = 'pa_d_dismissed_suggestions';

let suggestions = null;
let suggestionsLoading = false;
let suggestionsError = false;
let activeIndex = 0;
let mounted = false;
let refreshCollections = async () => {};
let notifyChange = () => {};
const creatingFingerprints = new Set();

function notifyChanged() {
    notifyChange();
    window.dispatchEvent(new CustomEvent('collection-suggestions:changed'));
}

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

export const suggestionFingerprint = (s) => (
    s.fingerprint || `${s.kind || ''}|${s.cover_image_id || ''}|${s.count || 0}`
);

export function suggestionFingerprints(suggestion) {
    const aliases = Array.isArray(suggestion?.fingerprint_aliases) ? suggestion.fingerprint_aliases : [];
    return [...new Set([suggestionFingerprint(suggestion), ...aliases].filter((value) => typeof value === 'string' && value))];
}

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
    return (suggestions || []).filter((suggestion) => !suggestionFingerprints(suggestion).some((fingerprint) => gone.has(fingerprint)));
}

const collapsedKinds = new Set();

function currentSuggestions() {
    // Grouped by kind (best-ranked kind first), ranking preserved within each group.
    const buckets = new Map();
    for (const suggestion of visibleSuggestions()) {
        const kind = suggestionKind(suggestion);
        if (!buckets.has(kind)) buckets.set(kind, []);
        buckets.get(kind).push(suggestion);
    }
    const visible = [...buckets.values()].flat();
    if (activeIndex >= visible.length) activeIndex = Math.max(0, visible.length - 1);
    return visible;
}

export async function loadSuggestionsOnce() {
    if (suggestions || suggestionsLoading) return;
    suggestionsLoading = true;
    suggestionsError = false;
    notifyChanged();
    try {
        const data = await getCollectionSuggestions(scopeParams());
        if (data && Array.isArray(data.suggestions)) suggestions = data.suggestions;
    } catch {
        suggestions = [];
        suggestionsError = true;
    } finally {
        suggestionsLoading = false;
        notifyChanged();
        if (mounted) render();
    }
}

export function suggestionsAreLoading() {
    return suggestionsLoading && suggestions == null;
}

export function openSuggestionsReview() {
    activeIndex = 0;
    setActiveLens('suggestions');
}

function closeSuggestionsReview() {
    setActiveLens('grid');
}

export async function createSuggestion(suggestion) {
    const fp = suggestionFingerprint(suggestion);
    const fingerprints = suggestionFingerprints(suggestion);
    if (creatingFingerprints.has(fp)) return false;
    creatingFingerprints.add(fp);
    try {
        fingerprints.forEach(dismissFingerprint);
        notifyChanged();
        render();
        const result = await createCollection(
            suggestion.title,
            suggestion.image_ids || [],
            suggestion.subtitle || '',
            suggestion.query || null,
        );
        if (!(result && result.ok)) {
            fingerprints.forEach(restoreFingerprint);
            notifyChanged();
            render();
            showToast("Couldn't create collection");
            return false;
        }
        await refreshCollections();
        showToast('Collection created');
        notifyChanged();
        render();
        return true;
    } finally {
        creatingFingerprints.delete(fp);
        render();
    }
}

export function dismissSuggestion(suggestion) {
    const fingerprints = suggestionFingerprints(suggestion);
    fingerprints.forEach(dismissFingerprint);
    notifyChanged();
    render();
    showToast('Dismissed', {
        undo: () => {
            fingerprints.forEach(restoreFingerprint);
            notifyChanged();
            render();
        },
    });
}

const KIND_META = {
    shoot: { label: 'Shoot', icon: 'camera' },
    event: { label: 'Event', icon: 'calendar-days' },
    cluster: { label: 'Cluster', icon: 'layers' },
    theme: { label: 'Theme', icon: 'tag' },
};

function suggestionKind(suggestion) {
    return KIND_META[suggestion?.kind] ? suggestion.kind : 'shoot';
}

function kindBadge(suggestion) {
    const kind = suggestionKind(suggestion);
    const meta = KIND_META[kind];
    return `<span class="suggest-kind-badge" data-kind="${kind}">${icon(meta.icon)}${esc(meta.label)}</span>`;
}

function liveMark(suggestion) {
    if (suggestion.mode !== 'smart' && !suggestion.query) return '';
    return `<span class="suggest-live-mark" title="Live collection">${icon('sparkles')} Live</span>`;
}

function evidenceHtml(suggestion) {
    const labels = (Array.isArray(suggestion.evidence) ? suggestion.evidence : [])
        .map((item) => String(item?.label || '').trim()).filter(Boolean).slice(0, 2);
    return labels.length ? `<span class="suggest-evidence" title="Why this collection">${esc(labels.join(' · '))}</span>` : '';
}

function changeHtml(suggestion) {
    const summary = String(suggestion.change_summary || '').trim();
    return summary ? `<span class="suggest-change">${esc(summary)}</span>` : '';
}

function rowHtml(suggestion, index) {
    const active = index === activeIndex;
    return `<button class="suggest-review-row ${active ? 'active' : ''}" data-suggest-index="${index}" type="button">`
        + `<span class="suggest-row-cover">${suggestion.cover_image_id ? `<img src="${esc(thumbUrl('sm', suggestion.cover_image_id))}" alt="">` : icon('sparkles')}</span>`
        + '<span class="suggest-row-copy">'
        + `<b title="${esc(suggestion.title)}">${liveMark(suggestion)}${esc(suggestion.title)}</b>`
        + `<span title="${esc(`${suggestion.reason || 'Suggested'} · ${fmt(suggestion.count)} photos`)}">${esc(suggestion.reason || 'Suggested')} · ${fmt(suggestion.count)} photos</span>`
        + '</span></button>';
}

function railHtml(visible) {
    const groups = [];
    visible.forEach((suggestion, index) => {
        const kind = suggestionKind(suggestion);
        const last = groups[groups.length - 1];
        if (last && last.kind === kind) last.rows.push(rowHtml(suggestion, index));
        else groups.push({ kind, rows: [rowHtml(suggestion, index)] });
    });
    return groups.map((group) => {
        const meta = KIND_META[group.kind];
        const collapsed = collapsedKinds.has(group.kind);
        return `<section class="psec suggest-rail-group ${collapsed ? 'collapsed' : ''}" data-kind-group="${group.kind}">`
            + '<div class="psec-head" tabindex="0">'
            + `<button class="psec-toggle" type="button" aria-label="Toggle ${meta.label}" aria-expanded="${collapsed ? 'false' : 'true'}" tabindex="-1">${icon('chevron-down')}</button>`
            + `<h3>${icon(meta.icon)}${esc(meta.label)}</h3>`
            + `<span class="num">${group.rows.length}</span></div>`
            + group.rows.join('')
            + '</section>';
    }).join('');
}

function previewHtml(suggestion) {
    const ids = (suggestion.image_ids || []).slice(0, 72);
    if (!ids.length) return '<div class="suggest-empty">No preview available.</div>';
    return ids.map((id) => (
        `<figure class="suggest-preview-thumb"><img loading="lazy" decoding="async" src="${esc(thumbUrl('sm', id))}" alt=""></figure>`
    )).join('');
}

function headerHtml(visibleCount = 0) {
    const copy = suggestionsLoading && suggestions == null
        ? 'Loading'
        : `${fmt(visibleCount)} ${visibleCount === 1 ? 'idea' : 'ideas'}`;
    return '<header id="suggestions-head">'
        + '<div><b>Suggested Collections</b>'
        + `<span id="suggestions-count" class="num">${esc(copy)}</span></div>`
        + `<button class="icon-btn" id="suggestions-close" data-tip="Grid (G / Esc)" aria-label="Return to Grid" type="button">${icon('x')}</button>`
        + '</header>';
}

function bindChrome(root) {
    root.querySelector('#suggestions-close')?.addEventListener('click', closeSuggestionsReview);
}

function render() {
    if (!mounted) return;
    const root = document.getElementById('suggestions-flow');
    const visible = currentSuggestions();
    if (suggestionsLoading && suggestions == null) {
        root.innerHTML = '<div class="suggest-review">'
            + headerHtml(visible.length)
            + '<aside class="suggest-review-rail">'
            + '<div class="skel suggest-review-skel"></div><div class="skel suggest-review-skel"></div>'
            + '</aside><section class="suggest-review-main"><div class="skel suggest-preview-skel"></div></section></div>';
        bindChrome(root);
        return;
    }

    if (suggestionsError) {
        root.innerHTML = '<div class="suggest-review empty">'
            + headerHtml(0)
            + '<div class="suggest-review-empty-body"><div>'
            + `<span class="suggest-empty-glyph">${icon('sparkles')}</span>`
            + '<h2>Couldn\'t load suggestions</h2>'
            + '<p>The archive did not respond.</p>'
            + '<div class="shared-empty-actions"><button class="btn" id="suggestions-retry" type="button">Try again</button></div>'
            + '</div></div></div>';
        bindChrome(root);
        root.querySelector('#suggestions-retry')?.addEventListener('click', () => {
            suggestions = null;
            loadSuggestionsOnce();
            render();
        });
        return;
    }

    if (!visible.length) {
        const cleared = Boolean((suggestions || []).length && dismissed().length);
        root.innerHTML = '<div class="suggest-review empty">'
            + headerHtml(visible.length)
            + '<div class="suggest-review-empty-body"><div>'
            + `<span class="suggest-empty-glyph">${icon('sparkles')}</span>`
            + '<h2>Nothing to review.</h2>'
            + `<p>${esc(cleared ? 'You’ve cleared all suggestions.' : 'Add photos first. New collection ideas will appear as the library finds patterns.')}</p>`
            + '</div></div></div>';
        bindChrome(root);
        return;
    }

    const suggestion = visible[activeIndex];
    const creating = creatingFingerprints.has(suggestionFingerprint(suggestion));
    const liveCopy = suggestion.mode === 'smart' || suggestion.query ? 'Live' : '';
    root.innerHTML = '<div class="suggest-review">'
        + headerHtml(visible.length)
        + `<aside class="suggest-review-rail">${railHtml(visible)}</aside>`
        + '<section class="suggest-review-main">'
        + '<header class="suggest-review-head">'
        + '<div>'
        + `<div class="suggest-head-meta">${kindBadge(suggestion)}${liveCopy ? `<span class="suggest-live-copy" title="Updates automatically">${icon('sparkles')}${esc(liveCopy)}</span>` : ''}</div>`
        + `<h2 title="${esc(suggestion.title)}">${esc(suggestion.title)}</h2>`
        + `<p>${esc(suggestion.reason || 'Suggested')} · ${esc(suggestion.subtitle || `${fmt(suggestion.count)} photos`)}</p>`
        + evidenceHtml(suggestion)
        + changeHtml(suggestion)
        + '</div>'
        + '<div class="suggest-review-actions">'
        + `<button class="btn primary" id="suggest-create" type="button" ${creating ? 'disabled' : ''}>Create collection</button>`
        + '<button class="btn" id="suggest-dismiss" type="button">Dismiss</button>'
        + '</div></header>'
        + `<div class="suggest-preview-grid">${previewHtml(suggestion)}</div>`
        + '</section></div>';

    bindChrome(root);
    for (const row of root.querySelectorAll('.suggest-review-row')) {
        row.addEventListener('click', () => {
            activeIndex = Number(row.dataset.suggestIndex || 0);
            render();
        });
    }
    for (const head of root.querySelectorAll('.suggest-rail-group .psec-head')) {
        const toggleGroup = () => {
            const kind = head.closest('.suggest-rail-group')?.dataset.kindGroup;
            if (!kind) return;
            if (collapsedKinds.has(kind)) collapsedKinds.delete(kind);
            else {
                collapsedKinds.add(kind);
                if (suggestionKind(visible[activeIndex]) === kind) {
                    const next = visible.findIndex((s) => !collapsedKinds.has(suggestionKind(s)));
                    if (next >= 0) activeIndex = next;
                }
            }
            render();
        };
        head.addEventListener('click', toggleGroup);
        head.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            toggleGroup();
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

function stepIndex(visible, from, direction) {
    // Arrow navigation skips rows hidden inside collapsed groups.
    for (let index = from + direction; index >= 0 && index < visible.length; index += direction) {
        if (!collapsedKinds.has(suggestionKind(visible[index]))) return index;
    }
    return from;
}

function handleKeydown(event) {
    if (!mounted || event.ctrlKey || event.metaKey || event.altKey) return;
    if (foregroundLayerOpen()) return;
    const visible = currentSuggestions();
    const key = event.key.toLowerCase();
    if (key === 'escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeSuggestionsReview();
    } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        event.stopImmediatePropagation();
        activeIndex = stepIndex(visible, activeIndex, 1);
        render();
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopImmediatePropagation();
        activeIndex = stepIndex(visible, activeIndex, -1);
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

function foregroundLayerOpen() {
    return registeredForegroundLayerOpen('suggestions');
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
