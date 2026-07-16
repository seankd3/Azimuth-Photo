import {
    getPeople, getPeopleStatus, ignorePerson, labelPerson, mergePeople, rejectMergeSuggestion,
} from './api.js';
import { navigateToScope, on, setRankingsMeta } from './state.js';
import { releaseFocus, trapFocus } from './focusTrap.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { personLabel as cleanPersonLabel, isUnnamedPersonLabel } from '../people_labels.js';
import { escapeHtml as esc, formatCount as fmt } from './dom.js';

let mounted = false;
let initialized = false;
let peopleData = null;
let loading = false;
let generation = 0;
let dragPersonId = null;
let pendingMerge = null;
let mergeSourceId = null;
let loadError = false;
let peopleStatus = null;
let reviewFocusIndex = 0;

const hiddenPeople = new Set();
const ignoreTimers = new Map();
const countFor = (person) => Number(person?.image_count || person?.photo_count || person?.face_count || 0);
const thumbFor = (person = {}) => person.face_thumb_url || person.thumb_url || person.image_thumb_url || '';
const displayLabel = (person, fallback = '') => cleanPersonLabel(person, fallback);
const isNamed = (person, fallback = '') => !isUnnamedPersonLabel(displayLabel(person, fallback));
const personActionLabel = (person) => (isNamed(person) ? displayLabel(person) : 'Add name');

function allPeople() {
    const sections = (peopleData && peopleData.sections) || {};
    const seen = new Map();
    for (const list of [
        peopleData?.people,
        peopleData?.persons,
        peopleData?.results,
        sections.named_people,
        sections.most_seen,
        sections.other_faces,
    ]) {
        for (const person of list || []) {
            if (person?.id != null && !seen.has(String(person.id))) seen.set(String(person.id), person);
        }
    }
    for (const suggestion of sections.needs_review || []) {
        for (const person of [suggestion.source, suggestion.target]) {
            if (person?.id != null && !seen.has(String(person.id))) seen.set(String(person.id), person);
        }
    }
    return [...seen.values()];
}

function visiblePeople(list) {
    return (list || []).filter((person) => person?.id != null && !hiddenPeople.has(String(person.id)));
}

function arrangedSections() {
    const sections = (peopleData && peopleData.sections) || {};
    const people = visiblePeople(allPeople());
    const named = people
        .filter((person) => isNamed(person))
        .sort((a, b) => displayLabel(a).localeCompare(displayLabel(b), undefined, { sensitivity: 'base' }));
    const unnamed = people
        .filter((person) => !isNamed(person))
        .sort((a, b) => countFor(b) - countFor(a));
    return {
        named,
        unnamed,
        review: (sections.needs_review || []).filter((item) => item && !item._done),
    };
}

function faceHtml(person, sizeClass = '') {
    const src = thumbFor(person);
    return `<span class="person-face ${sizeClass}">${src ? `<img src="${esc(src)}" loading="lazy" decoding="async" alt="">` : `<span class="person-empty">${icon('users')}</span>`}</span>`;
}

function personCard(person) {
    const named = isNamed(person);
    const label = personActionLabel(person);
    return `<article class="person-card ${named ? 'is-named' : 'is-unnamed'}" data-person-id="${esc(person.id)}" tabindex="0" draggable="true">`
        + '<div class="person-card-top">'
        + `<button class="person-kebab" data-act="menu" data-tip="Person actions" aria-label="Person actions">${icon('ellipsis')}</button>`
        + '<div class="person-menu" role="menu">'
        + `<button data-act="rename" role="menuitem">${icon('pencil')}<span>Rename</span></button>`
        + `<button data-act="merge-start" role="menuitem">${icon('users')}<span>Merge with...</span></button>`
        + '<button data-act="ignore" role="menuitem">'
        + `${icon('eye')}<span>Hide person</span></button></div></div>`
        + faceHtml(person)
        + `<button class="person-title ${named ? '' : 'add-name'}" data-act="rename" title="${esc(label)}">${esc(label)}</button>`
        + `<div class="person-count">${fmt(countFor(person))} photos</div>`
        + '<div class="person-hover-actions" aria-hidden="true">'
        + `<span>${icon('pencil')} Rename</span><span>${icon('grip')} Drag to merge</span></div>`
        + '</article>';
}

function suggestionLabel(person, fallback = '') {
    return isNamed(person, fallback) ? displayLabel(person, fallback) : 'Add name';
}

function personMini(person, fallback = '') {
    const label = suggestionLabel(person, fallback);
    return `<button class="person-mini" data-person-id="${esc(person?.id || '')}" aria-label="${esc(label)}">`
        + faceHtml(person, 'mini')
        + `<span>${esc(label)}</span></button>`;
}

function reviewCard(suggestion) {
    const source = suggestion.source || { id: suggestion.source_person_id, label: suggestion.source_label };
    const target = suggestion.target || { id: suggestion.target_person_id, label: suggestion.target_label };
    const confidence = Math.round(Number(suggestion.confidence || 0) * 100);
    return `<article class="review-card" data-suggestion-id="${esc(suggestion.id)}" data-source-id="${esc(suggestion.source_person_id)}" data-target-id="${esc(suggestion.target_person_id)}" tabindex="0">`
        + '<div class="review-pair">'
        + personMini(source, suggestion.source_label)
        + '<span class="review-link" aria-hidden="true"></span>'
        + personMini(target, suggestion.target_label)
        + '</div>'
        + '<div class="review-copy"><b>Same person?</b>'
        + `<span>${confidence ? `${confidence}% match` : 'Suggested match'}</span></div>`
        + '<div class="review-actions">'
        + '<button class="btn primary" data-act="merge">Merge</button>'
        + '<button class="btn" data-act="reject">Not the same</button>'
        + '</div></article>';
}

function reviewStripHtml(items) {
    if (!items.length) return '';
    return '<section class="people-review-strip" data-section="needs_review">'
        + '<div class="people-sec-head"><div><h2>Review merges</h2>'
        + `<p>${fmt(items.length)} suggested ${items.length === 1 ? 'match' : 'matches'}</p></div></div>`
        + `<div class="review-rail">${items.map(reviewCard).join('')}</div></section>`;
}

function sectionHtml(title, items) {
    if (!items.length) return '';
    const photos = items.reduce((sum, person) => sum + countFor(person), 0);
    return `<section class="people-sec"><div class="people-sec-head"><div><h2>${esc(title)}</h2>`
        + `<p>${fmt(items.length)} ${items.length === 1 ? 'person' : 'people'} · ${fmt(photos)} photos</p></div></div>`
        + `<div class="people-grid">${items.map(personCard).join('')}</div></section>`;
}

function render() {
    if (!mounted) return;
    const flow = document.getElementById('people-flow');
    if (loading && !peopleData) {
        flow.innerHTML = '<section class="people-sec"><div class="people-sec-head"><div><h2>Loading faces</h2><p>Building people view</p></div></div><div class="people-grid">'
            + Array.from({ length: 18 }, () => '<div class="person-card"><div class="person-face skel"></div><div class="skel" style="width:72px;height:12px"></div><div class="skel" style="width:46px;height:10px"></div></div>').join('')
            + '</div></section>';
        return;
    }
    if (loadError) {
        flow.innerHTML = '<div class="load-error"><h4>Couldn\'t load People</h4><p>The archive did not respond. Try again.</p><button class="btn" id="people-retry">Try again</button></div>';
        flow.querySelector('#people-retry')?.addEventListener('click', load);
        return;
    }
    const { named, unnamed, review } = arrangedSections();
    const emptyCopy = peopleStatus && String(peopleStatus.state || peopleStatus.status || '').toLowerCase().includes('paused')
        ? 'Add photos first, then resume face scanning to find people.'
        : 'Add a source if your library is empty. People will appear after face scanning.';
    flow.innerHTML = [
        reviewStripHtml(review),
        sectionHtml('Named people', named),
        sectionHtml('Unnamed', unnamed),
    ].join('') || `<div class="grid-empty"><h3>No people yet</h3><p>${esc(emptyCopy)}</p></div>`;
    if (mergeSourceId) {
        flow.querySelector(`.person-card[data-person-id="${CSS.escape(String(mergeSourceId))}"]`)?.classList.add('merge-source');
    }
    bindLoadedImages(flow);
}

function bindLoadedImages(root) {
    for (const img of root.querySelectorAll('.person-face img')) {
        if (img.complete) img.classList.add('ld');
        else img.addEventListener('load', () => img.classList.add('ld'), { once: true });
    }
}

async function load() {
    if (!mounted || loading) return;
    loading = true;
    loadError = false;
    const seq = ++generation;
    render();
    try {
        const [data, status] = await Promise.all([getPeople(500), getPeopleStatus()]);
        if (seq !== generation) return;
        peopleStatus = status;
        if (!data) throw new Error('People response was empty');
        peopleData = data;
        const total = allPeople().length;
        setRankingsMeta({ visibleImages: total, sortQuality: null });
    } catch {
        if (seq !== generation) return;
        peopleData = null;
        loadError = true;
        setRankingsMeta({ visibleImages: 0, sortQuality: null });
    } finally {
        if (seq !== generation) return;
        loading = false;
        render();
    }
}

function findPerson(id) {
    return allPeople().find((item) => String(item.id) === String(id)) || null;
}

function updatePerson(id, patch) {
    for (const person of allPeople()) {
        if (String(person.id) === String(id)) Object.assign(person, patch);
    }
}

function removeSuggestion(suggestionId) {
    const review = peopleData?.sections?.needs_review || [];
    const suggestion = review.find((item) => String(item.id) === String(suggestionId));
    if (suggestion) suggestion._done = true;
}

function reviewCards() {
    return [...document.querySelectorAll('#people-flow .review-card[data-suggestion-id]')];
}

function reviewCardIndex(card) {
    const cards = reviewCards();
    return Math.max(0, cards.indexOf(card));
}

function focusReviewCard(index = reviewFocusIndex) {
    const cards = reviewCards();
    if (!cards.length) return false;
    reviewFocusIndex = Math.max(0, Math.min(cards.length - 1, Number(index) || 0));
    cards[reviewFocusIndex]?.focus({ preventScroll: true });
    return true;
}

function openPerson(person) {
    if (!person || !person.id) return;
    navigateToScope({
        people: person.id,
        personLabel: isNamed(person) ? displayLabel(person) : '',
        personThumb: thumbFor(person),
    });
}

function closeMenus() {
    for (const card of document.querySelectorAll('.person-card.menu-open')) {
        releaseFocus(card.querySelector('.person-menu'));
        card.classList.remove('menu-open');
    }
}

function closeMergePopover() {
    const pop = document.getElementById('people-merge-pop');
    if (pop) {
        releaseFocus(pop);
        pop.remove();
    }
    pendingMerge = null;
}

function mergeSourceName() {
    return personActionLabel(findPerson(mergeSourceId) || { label: 'person' });
}

function syncMergeBanner() {
    let banner = document.getElementById('people-merge-banner');
    if (!mergeSourceId) {
        banner?.remove();
        return;
    }
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'people-merge-banner';
        banner.className = 'people-merge-banner';
        banner.setAttribute('role', 'status');
        document.body.appendChild(banner);
    }
    banner.textContent = `Merging ${mergeSourceName()} — click a person to merge into, Esc cancels`;
}

function disarmMerge() {
    closeMergePopover();
    mergeSourceId = null;
    syncMergeBanner();
}

function focusPersonCard(personId) {
    requestAnimationFrame(() => {
        const id = String(personId || '');
        if (!id) return;
        const card = document.querySelector(`.person-card[data-person-id="${CSS.escape(id)}"]`);
        if (card) card.focus({ preventScroll: true });
    });
}

function beginRename(card, person) {
    if (!person) return;
    const target = card.querySelector('.person-title');
    const previous = isNamed(person) ? displayLabel(person) : '';
    card.classList.add('renaming');
    target.outerHTML = '<form class="person-rename" data-act="rename-form">'
        + `<input value="${esc(previous)}" placeholder="Add name" aria-label="Person name">`
        + `<button type="submit" aria-label="Save name">${icon('check')}</button>`
        + `<button type="button" data-act="cancel-rename" aria-label="Cancel rename">${icon('x')}</button></form>`;
    const form = card.querySelector('.person-rename');
    const input = form.querySelector('input');
    input.focus();
    input.select();
}

async function commitRename(form, person) {
    if (!person) return;
    const input = form.querySelector('input');
    const next = input.value.trim();
    const previous = {
        name: person.name || '',
        label: person.label || '',
        display_name: person.display_name || '',
        person_label: person.person_label || '',
    };
    if (!next) {
        const personId = person.id;
        render();
        focusPersonCard(personId);
        return;
    }
    updatePerson(person.id, { name: next, label: next });
    render();
    focusPersonCard(person.id);
    const result = await labelPerson(person.id, next);
    if (result && result.ok) {
        showToast(`Renamed to "${next}"`);
    } else {
        updatePerson(person.id, previous);
        render();
        focusPersonCard(person.id);
        showToast("Couldn't rename person");
    }
}

function requestIgnore(card, person) {
    if (!person) return;
    const id = String(person.id);
    hiddenPeople.add(id);
    render();
    const duration = 8000;
    const timer = window.setTimeout(async () => {
        ignoreTimers.delete(id);
        const result = await ignorePerson(person.id);
        if (!result || !result.ok) {
            hiddenPeople.delete(id);
            render();
            showToast("Couldn't hide person");
        }
    }, duration);
    ignoreTimers.set(id, timer);
    showToast('Person hidden', {
        duration,
        undo: () => {
            window.clearTimeout(ignoreTimers.get(id));
            ignoreTimers.delete(id);
            hiddenPeople.delete(id);
            render();
        },
    });
}

async function runMerge(sourceId, targetId, trigger = null, options = {}) {
    if (!sourceId || !targetId || String(sourceId) === String(targetId)) return false;
    if (String(mergeSourceId) === String(sourceId)) disarmMerge();
    trigger?.classList.add('is-pending');
    const result = await mergePeople(sourceId, targetId);
    if (result && result.ok) {
        showToast('People merged');
        disarmMerge();
        if (options.suggestionId) {
            removeSuggestion(options.suggestionId);
            render();
            focusReviewCard(options.reviewIndex);
        } else {
            peopleData = null;
        }
        load();
        return true;
    } else {
        trigger?.classList.remove('is-pending');
        showToast("Couldn't merge people");
        return false;
    }
}

async function handleReview(button, card) {
    const action = button.dataset.act;
    const suggestionId = card.dataset.suggestionId;
    const sourceId = card.dataset.sourceId;
    const targetId = card.dataset.targetId;
    reviewFocusIndex = reviewCardIndex(card);
    if (action === 'merge') {
        card.classList.add('is-pending');
        const merged = await runMerge(sourceId, targetId, button, { suggestionId, reviewIndex: reviewFocusIndex });
        if (!merged) card.classList.remove('is-pending');
        return;
    }
    card.classList.add('is-pending');
    const result = await rejectMergeSuggestion(suggestionId);
    if (result && result.ok) {
        removeSuggestion(suggestionId);
        showToast('Merge suggestion dismissed');
        render();
        focusReviewCard(reviewFocusIndex);
        load();
    } else {
        card.classList.remove('is-pending');
        showToast("Couldn't reject suggestion");
    }
}

export function reviewPeopleMergeByKey(action) {
    if (!mounted || document.getElementById('people-merge-pop')) return false;
    const active = document.activeElement?.closest?.('.review-card[data-suggestion-id]');
    const card = active || reviewCards()[reviewFocusIndex] || reviewCards()[0];
    if (!card) return false;
    reviewFocusIndex = reviewCardIndex(card);
    const selector = action === 'merge' ? '[data-act="merge"]' : '[data-act="reject"]';
    const button = card.querySelector(selector);
    if (!button || button.disabled || card.classList.contains('is-pending')) return false;
    handleReview(button, card);
    return true;
}

function mergePopoverHtml(source, target, options = {}) {
    const sourceLabel = personActionLabel(source);
    const targetLabel = personActionLabel(target);
    const sourceCount = countFor(source);
    const targetCount = countFor(target);
    return '<div id="people-merge-pop" role="dialog" aria-label="Confirm merge">'
        + `<b>Merge ${esc(sourceLabel)} into ${esc(targetLabel)}</b>`
        + `<p>${fmt(sourceCount + targetCount)} photos will use one identity. The target keeps the name.</p>`
        + '<div><button class="btn primary" data-act="confirm-drop-merge">Merge</button>'
        + '<button class="btn" data-act="cancel-drop-merge">Cancel</button></div></div>';
}

function showMergePopover(sourceId, targetId, rect, options = {}) {
    const source = findPerson(sourceId) || { id: sourceId, label: options.sourceLabel || '' };
    const target = findPerson(targetId) || { id: targetId, label: options.targetLabel || '' };
    if (!source || !target) return;
    closeMergePopover();
    pendingMerge = { sourceId, targetId, suggestionId: options.suggestionId || null };
    document.body.insertAdjacentHTML('beforeend', mergePopoverHtml(source, target, options));
    const pop = document.getElementById('people-merge-pop');
    const left = Math.min(window.innerWidth - 280, Math.max(12, rect.left + rect.width / 2 - 130));
    const top = Math.min(window.innerHeight - 140, Math.max(12, rect.top + 12));
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    trapFocus(pop, pop.querySelector('button'));
}

function clearDropTargets() {
    for (const card of document.querySelectorAll('.person-card.drop-target')) card.classList.remove('drop-target');
}

export function initPeople() {
    if (initialized) return;
    initialized = true;
    const flow = document.getElementById('people-flow');
    flow.addEventListener('click', (event) => {
        const reviewButton = event.target.closest('.review-card button[data-act]');
        if (reviewButton) {
            handleReview(reviewButton, reviewButton.closest('.review-card'));
            return;
        }
        const mini = event.target.closest('.person-mini[data-person-id]');
        if (mini && mini.dataset.personId) {
            openPerson(findPerson(mini.dataset.personId) || { id: mini.dataset.personId });
            return;
        }
        const card = event.target.closest('.person-card[data-person-id]');
        if (!card) return;
        const person = findPerson(card.dataset.personId);
        const action = event.target.closest('[data-act]')?.dataset.act;
        if (action === 'menu') {
            event.stopPropagation();
            const open = card.classList.contains('menu-open');
            closeMenus();
            if (!open) {
                card.classList.add('menu-open');
                const menu = card.querySelector('.person-menu');
                trapFocus(menu, menu?.querySelector('button'));
            }
        } else if (action === 'rename') {
            event.stopPropagation();
            closeMenus();
            beginRename(card, person);
        } else if (action === 'merge-start') {
            event.stopPropagation();
            closeMenus();
            if (String(mergeSourceId) === String(card.dataset.personId)) {
                disarmMerge();
                return;
            }
            mergeSourceId = card.dataset.personId;
            render();
            focusPersonCard(mergeSourceId);
            syncMergeBanner();
        } else if (action === 'ignore') {
            event.stopPropagation();
            requestIgnore(card, person);
        } else if (action === 'cancel-rename') {
            event.stopPropagation();
            const personId = card.dataset.personId;
            render();
            focusPersonCard(personId);
        } else if (mergeSourceId && String(mergeSourceId) === String(card.dataset.personId) && !event.target.closest('form')) {
            event.stopPropagation();
            disarmMerge();
            render();
            focusPersonCard(card.dataset.personId);
        } else if (mergeSourceId && String(mergeSourceId) !== String(card.dataset.personId) && !event.target.closest('form')) {
            event.stopPropagation();
            showMergePopover(mergeSourceId, card.dataset.personId, card.getBoundingClientRect());
        } else if (!event.target.closest('form')) {
            openPerson(person);
        }
    });
    flow.addEventListener('submit', (event) => {
        const form = event.target.closest('.person-rename');
        if (!form) return;
        event.preventDefault();
        commitRename(form, findPerson(form.closest('.person-card')?.dataset.personId));
    });
    flow.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && event.target.closest('.person-rename')) {
            event.preventDefault();
            event.stopPropagation();
            const personId = event.target.closest('.person-card')?.dataset.personId;
            render();
            focusPersonCard(personId);
            return;
        }
        if (event.key === 'Escape' && event.target.closest('.person-card.menu-open')) {
            event.preventDefault();
            event.stopPropagation();
            closeMenus();
            return;
        }
        if (event.key !== 'Enter' || event.target.closest('input, button')) return;
        const card = event.target.closest('.person-card[data-person-id]');
        if (card) {
            event.preventDefault();
            event.stopPropagation();
            openPerson(findPerson(card.dataset.personId));
        }
    });
    flow.addEventListener('dragstart', (event) => {
        const card = event.target.closest('.person-card[data-person-id]');
        if (!card) return;
        dragPersonId = card.dataset.personId;
        card.classList.add('dragging');
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', dragPersonId);
    });
    flow.addEventListener('dragover', (event) => {
        const card = event.target.closest('.person-card[data-person-id]');
        if (!card || !dragPersonId || card.dataset.personId === dragPersonId) return;
        event.preventDefault();
        clearDropTargets();
        card.classList.add('drop-target');
        event.dataTransfer.dropEffect = 'move';
    });
    flow.addEventListener('dragleave', (event) => {
        const card = event.target.closest('.person-card.drop-target');
        if (card && !card.contains(event.relatedTarget)) card.classList.remove('drop-target');
    });
    flow.addEventListener('drop', (event) => {
        const card = event.target.closest('.person-card[data-person-id]');
        if (!card || !dragPersonId || card.dataset.personId === dragPersonId) return;
        event.preventDefault();
        clearDropTargets();
        showMergePopover(dragPersonId, card.dataset.personId, card.getBoundingClientRect());
    });
    flow.addEventListener('dragend', () => {
        dragPersonId = null;
        clearDropTargets();
        flow.querySelector('.person-card.dragging')?.classList.remove('dragging');
    });
    document.addEventListener('click', (event) => {
        if (!event.target.closest('.person-card')) closeMenus();
        const pop = document.getElementById('people-merge-pop');
        if (pop && !pop.contains(event.target) && !event.target.closest('.person-card')) closeMergePopover();
    });
    document.addEventListener('click', (event) => {
        const confirm = event.target.closest('[data-act="confirm-drop-merge"]');
        const cancel = event.target.closest('[data-act="cancel-drop-merge"]');
        if (confirm && pendingMerge) {
            const merge = pendingMerge;
            closeMergePopover();
            runMerge(merge.sourceId, merge.targetId, null, {
                suggestionId: merge.suggestionId,
                reviewIndex: reviewFocusIndex,
            });
            if (merge.suggestionId) removeSuggestion(merge.suggestionId);
        } else if (cancel) {
            closeMergePopover();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (mergeSourceId) {
            const sourceId = mergeSourceId;
            event.preventDefault();
            event.stopPropagation();
            disarmMerge();
            render();
            focusPersonCard(sourceId);
            return;
        }
        if (document.getElementById('people-merge-pop')) {
            event.preventDefault();
            event.stopPropagation();
            closeMergePopover();
        }
    });
    on('scope', () => {
        if (mounted) setRankingsMeta({ visibleImages: 0, sortQuality: null });
    });
}

export function mountPeople() {
    mounted = true;
    document.getElementById('view-people').classList.add('active');
    if (!peopleData) load();
    else render();
}

export function unmountPeople() {
    mounted = false;
    generation += 1;
    loading = false;
    document.getElementById('view-people').classList.remove('active');
    disarmMerge();
}
