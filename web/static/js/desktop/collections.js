import { createCollection, listCollections, thumbUrl } from './api.js';
import { emit, navigateToScope, on } from './state.js';
import { openCollectionActions, openDeliverOverlay } from './panel.js';
import {
    createSuggestion, dismissSuggestion, loadSuggestionsOnce, openSuggestionsReview,
    suggestionsAreLoading, visibleSuggestions,
} from './suggestions.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { esc } from './dom.js';

const fmt = (value) => Number(value || 0).toLocaleString('en-US');
let mounted = false;
let initialized = false;
let collections = [];
let loading = true;
let loadError = false;
let filter = '';
let creating = false;

function collectionCover(collection) {
    if (!collection.cover_image_id) {
        return `<span class="collection-cover-empty">${icon(collection.smart ? 'sparkles' : 'image')}</span>`;
    }
    return `<span class="collection-cover-empty">${icon('image')}</span>`
        + `<img src="${esc(thumbUrl('md', collection.cover_image_id))}" loading="lazy" decoding="async" alt="">`;
}

function collectionStatus(collection) {
    const badges = [];
    if (collection.smart) badges.push(`<span class="collection-badge live">${icon('sparkles')} Live</span>`);
    if (collection.published) badges.push(`<span class="collection-badge published">${icon('globe')} Published</span>`);
    return badges.join('');
}

function collectionCard(collection) {
    return `<article class="collection-card" data-collection-id="${collection.id}">`
        + `<button class="collection-card-open" type="button" aria-label="Open ${esc(collection.name)}">`
        + `<span class="collection-card-cover">${collectionCover(collection)}<span class="collection-card-shade"></span>`
        + `<span class="collection-card-count">${fmt(collection.image_count)} ${Number(collection.image_count) === 1 ? 'photo' : 'photos'}</span></span>`
        + `<span class="collection-card-copy"><strong title="${esc(collection.name)}">${esc(collection.name)}</strong>`
        + `<span>${collectionStatus(collection)}</span></span></button>`
        + '<div class="collection-card-actions">'
        + `<button class="collection-deliver" type="button" data-tip="Deliver" aria-label="Deliver ${esc(collection.name)}">${icon('send')}</button>`
        + `<button class="collection-card-menu" type="button" data-tip="Manage" aria-label="Manage ${esc(collection.name)}">${icon('ellipsis')}</button>`
        + '</div></article>';
}

function suggestionCard(suggestion, index) {
    const cover = suggestion.cover_image_id
        ? `<span>${icon('sparkles')}</span><img src="${esc(thumbUrl('md', suggestion.cover_image_id))}" loading="lazy" decoding="async" alt="">`
        : `<span>${icon('sparkles')}</span>`;
    return `<article class="collection-suggestion" data-suggestion-index="${index}">`
        + `<div class="collection-suggestion-cover">${cover}<span class="collection-suggestion-kind">${esc(suggestion.kind || 'idea')}</span></div>`
        + '<div class="collection-suggestion-copy">'
        + `<div><strong title="${esc(suggestion.title)}">${esc(suggestion.title)}</strong>`
        + `<span>${esc(suggestion.reason || 'Suggested from your library')} · ${fmt(suggestion.count)} photos</span>`
        + suggestionSignals(suggestion)
        + suggestionChange(suggestion)
        + '</div>'
        + '<div class="collection-suggestion-actions">'
        + `<button class="btn primary" data-suggestion-create="${index}" type="button">Create</button>`
        + `<button class="icon-btn" data-suggestion-dismiss="${index}" data-tip="Dismiss" aria-label="Dismiss ${esc(suggestion.title)}" type="button">${icon('x')}</button>`
        + '</div></div></article>';
}

function suggestionSignals(suggestion) {
    const labels = (Array.isArray(suggestion.evidence) ? suggestion.evidence : [])
        .map((item) => String(item?.label || '').trim()).filter(Boolean).slice(0, 2);
    return labels.length ? `<small class="collection-suggestion-evidence">${esc(labels.join(' · '))}</small>` : '';
}

function suggestionChange(suggestion) {
    const summary = String(suggestion.change_summary || '').trim();
    return summary ? `<small class="collection-suggestion-change">${esc(summary)}</small>` : '';
}

function createCard() {
    if (!creating) {
        return `<button class="collection-create-card" id="collection-create-start" type="button">`
            + `<span>${icon('plus')}</span><strong>New collection</strong><small>Start an album or client set</small></button>`;
    }
    return '<form class="collection-create-card editing" id="collection-create-form">'
        + `<span>${icon('folder-plus')}</span><label for="collection-create-name">Name your collection</label>`
        + '<input id="collection-create-name" maxlength="160" autocomplete="off" placeholder="Untitled collection">'
        + '<div><button class="btn primary" type="submit">Create</button><button class="btn" id="collection-create-cancel" type="button">Cancel</button></div></form>';
}

function filteredCollections() {
    const query = filter.trim().toLocaleLowerCase();
    if (!query) return collections;
    return collections.filter((collection) => String(collection.name || '').toLocaleLowerCase().includes(query));
}

function renderSuggestions() {
    if (suggestionsAreLoading()) {
        return '<section class="collections-suggestions"><div class="collections-section-head"><div><span class="collections-eyebrow">Found for you</span><h2>Collection ideas</h2></div></div>'
            + '<div class="collection-suggestion-grid"><div class="collection-suggestion skel"></div><div class="collection-suggestion skel"></div></div></section>';
    }
    const suggestions = visibleSuggestions();
    if (!suggestions.length) return '';
    const shown = suggestions.slice(0, 3);
    return '<section class="collections-suggestions"><div class="collections-section-head"><div><span class="collections-eyebrow">Found for you</span><h2>Collection ideas</h2></div>'
        + `<button class="btn" id="collections-review-all" type="button">Review all <span class="num">${fmt(suggestions.length)}</span></button></div>`
        + `<div class="collection-suggestion-grid">${shown.map(suggestionCard).join('')}</div></section>`;
}

function renderBody() {
    if (loading) {
        return '<div class="collections-grid"><div class="collection-create-card skel"></div>'
            + Array.from({ length: 5 }, () => '<div class="collection-card skel"></div>').join('') + '</div>';
    }
    if (loadError) {
        return '<div class="collections-empty"><span>' + icon('folder') + '</span><h2>Couldn\'t load collections</h2>'
            + '<p>The archive did not respond.</p><button class="btn" id="collections-retry" type="button">Try again</button></div>';
    }
    const visible = filteredCollections();
    if (filter && !visible.length) {
        return '<div class="collections-empty compact"><span>' + icon('search') + '</span><h2>No matching collections</h2><p>Try another name.</p></div>';
    }
    return `<div class="collections-grid">${createCard()}${visible.map(collectionCard).join('')}</div>`;
}

function render() {
    if (!mounted) return;
    const root = document.getElementById('collections-flow');
    root.innerHTML = '<div class="collections-page">'
        + '<header class="collections-header"><div><span class="collections-eyebrow">Your library, shaped</span><h1>Collections</h1>'
        + `<p>${fmt(collections.length)} ${collections.length === 1 ? 'collection' : 'collections'} · Build stories, client sets, and living views.</p></div>`
        + '<label class="collections-filter"><span>' + icon('search') + '</span><input id="collections-filter" type="search" autocomplete="off" placeholder="Filter collections" aria-label="Filter collections"></label></header>'
        + renderSuggestions()
        + '<section class="collections-library"><div class="collections-section-head"><div><span class="collections-eyebrow">Library</span><h2>All collections</h2></div></div>'
        + renderBody() + '</section></div>';
    bind(root);
}

function openCollection(collection) {
    navigateToScope({
        collectionId: collection.id,
        collectionName: collection.name || 'Collection',
        collectionSmart: Boolean(collection.smart),
    });
}

function bindCollectionCards(root) {
    for (const card of root.querySelectorAll('.collection-card[data-collection-id]')) {
        const collection = collections.find((item) => Number(item.id) === Number(card.dataset.collectionId));
        if (!collection) continue;
        card.querySelector('.collection-card-open')?.addEventListener('click', () => openCollection(collection));
        card.querySelector('.collection-deliver')?.addEventListener('click', (event) => {
            openDeliverOverlay(collection.id, collection.name || 'Collection', event.currentTarget);
        });
        card.querySelector('.collection-card-menu')?.addEventListener('click', (event) => {
            openCollectionActions(collection, event.currentTarget);
        });
    }
}

function bindSuggestions(root) {
    root.querySelector('#collections-review-all')?.addEventListener('click', openSuggestionsReview);
    const suggestions = visibleSuggestions();
    for (const button of root.querySelectorAll('[data-suggestion-create]')) {
        button.addEventListener('click', async () => {
            button.disabled = true;
            await createSuggestion(suggestions[Number(button.dataset.suggestionCreate)]);
        });
    }
    for (const button of root.querySelectorAll('[data-suggestion-dismiss]')) {
        button.addEventListener('click', () => dismissSuggestion(suggestions[Number(button.dataset.suggestionDismiss)]));
    }
}

function bindCreate(root) {
    root.querySelector('#collection-create-start')?.addEventListener('click', () => {
        creating = true;
        render();
        document.getElementById('collection-create-name')?.focus();
    });
    root.querySelector('#collection-create-cancel')?.addEventListener('click', () => {
        creating = false;
        render();
    });
    root.querySelector('#collection-create-form')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = event.currentTarget.querySelector('input');
        const name = input.value.trim();
        if (!name) return input.focus();
        const submit = event.currentTarget.querySelector('[type="submit"]');
        submit.disabled = true;
        const result = await createCollection(name, []);
        if (!result?.ok) {
            submit.disabled = false;
            showToast("Couldn't create collection");
            return;
        }
        creating = false;
        showToast(`Created “${name}”`);
        emit('collections:refresh');
        await load();
    });
}

function bind(root) {
    for (const image of root.querySelectorAll('img')) {
        image.addEventListener('error', () => image.remove(), { once: true });
    }
    const filterInput = root.querySelector('#collections-filter');
    filterInput.value = filter;
    filterInput.addEventListener('input', () => {
        filter = filterInput.value;
        render();
        document.getElementById('collections-filter')?.focus();
    });
    root.querySelector('#collections-retry')?.addEventListener('click', load);
    bindCollectionCards(root);
    bindSuggestions(root);
    bindCreate(root);
}

async function load() {
    loading = true;
    loadError = false;
    render();
    try {
        const payload = await listCollections();
        collections = payload?.collections || [];
    } catch {
        collections = [];
        loadError = true;
    } finally {
        loading = false;
        render();
    }
}

export function mountCollections() {
    mounted = true;
    document.getElementById('view-collections').classList.add('active');
    load();
    loadSuggestionsOnce();
}

export function unmountCollections() {
    mounted = false;
    document.getElementById('view-collections').classList.remove('active');
}

export function initCollections() {
    if (initialized) return;
    initialized = true;
    on('collections:changed', ({ collections: next } = {}) => {
        if (Array.isArray(next)) collections = next;
        render();
    });
    window.addEventListener('collection-suggestions:changed', render);
}
