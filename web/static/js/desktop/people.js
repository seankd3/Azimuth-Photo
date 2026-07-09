import {
    getPeople, ignorePerson, labelPerson, mergePeople, rejectMergeSuggestion,
} from './api.js';
import { on, setActiveLens, setRankingsMeta, setScope } from './state.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { personLabel as cleanPersonLabel, isUnnamedPersonLabel } from '../people_labels.js';

let mounted = false;
let initialized = false;
let peopleData = null;
let loading = false;
let generation = 0;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (n) => Number(n || 0).toLocaleString('en-US');

function personCard(person, section) {
    const label = cleanPersonLabel(person);
    const named = !isUnnamedPersonLabel(label);
    return `<article class="person-card" data-person-id="${person.id}" data-section="${section}" tabindex="0">`
        + `<div class="person-face">${person.face_thumb_url || person.thumb_url ? `<img src="${esc(person.face_thumb_url || person.thumb_url)}" loading="lazy" decoding="async" alt="">` : `<div class="person-empty">${icon('users')}</div>`}</div>`
        + `<div class="person-name ${named ? '' : 'unnamed'}">${esc(label)}</div>`
        + `<div class="person-count">${fmt(person.photo_count || person.image_count || person.face_count)} photos</div>`
        + '<div class="person-actions"><button data-act="rename">Rename</button><button data-act="ignore">Ignore</button></div></article>';
}

function reviewCard(suggestion) {
    const source = suggestion.source || {};
    const target = suggestion.target || {};
    return `<article class="review-card" data-suggestion-id="${suggestion.id}" data-source-id="${suggestion.source_person_id}" data-target-id="${suggestion.target_person_id}">`
        + '<div class="review-faces">'
        + `${personMini(source, suggestion.source_label)}${personMini(target, suggestion.target_label)}</div>`
        + `<div class="review-copy"><b>${esc(cleanPersonLabel(source, suggestion.source_label))} + ${esc(cleanPersonLabel(target, suggestion.target_label))}</b><span>${Math.round(Number(suggestion.confidence || 0) * 100)}% match</span></div>`
        + '<div class="review-actions"><button data-act="merge">Merge</button><button data-act="reject">Reject</button></div></article>';
}

function personMini(person, fallback) {
    const src = person.face_thumb_url || person.thumb_url || '';
    return `<button class="person-mini" data-person-id="${person.id || ''}" aria-label="${esc(cleanPersonLabel(person, fallback))}">`
        + `${src ? `<img src="${esc(src)}" alt="">` : `<span>${icon('users')}</span>`}</button>`;
}

function sectionHtml(title, key, items) {
    if (!items || !items.length) return '';
    const body = key === 'needs_review'
        ? `<div class="review-grid">${items.map(reviewCard).join('')}</div>`
        : `<div class="people-grid">${items.map((person) => personCard(person, key)).join('')}</div>`;
    return `<section class="people-sec" data-section="${key}"><h2>${title} <span class="num">· ${fmt(items.length)}</span></h2>${body}</section>`;
}

function render() {
    if (!mounted) return;
    const flow = document.getElementById('people-flow');
    if (loading && !peopleData) {
        flow.innerHTML = '<section class="people-sec"><h2>Loading faces</h2><div class="people-grid">'
            + Array.from({ length: 16 }, () => '<div class="person-card"><div class="person-face skel"></div><div class="skel" style="width:72px;height:12px"></div></div>').join('')
            + '</div></section>';
        return;
    }
    const sections = (peopleData && peopleData.sections) || {};
    const html = [
        sectionHtml('Named', 'named_people', sections.named_people),
        sectionHtml('Most seen', 'most_seen', sections.most_seen),
        sectionHtml('Needs review', 'needs_review', sections.needs_review),
        sectionHtml('Other faces', 'other_faces', sections.other_faces),
    ].join('');
    flow.innerHTML = html || '<div class="load-error"><h4>No people yet</h4><p>People will appear here after face scanning finds reusable identities.</p></div>';
    for (const img of flow.querySelectorAll('.person-face img')) {
        img.addEventListener('load', () => img.classList.add('ld'), { once: true });
    }
}

async function load() {
    if (!mounted || loading) return;
    loading = true;
    const seq = ++generation;
    render();
    const data = await getPeople(48);
    if (seq !== generation) return;
    loading = false;
    peopleData = data;
    const total = Number(data && data.counts && data.counts.people) || 0;
    setRankingsMeta({ visibleImages: total, sortQuality: null });
    render();
}

function findPerson(id) {
    const sections = (peopleData && peopleData.sections) || {};
    for (const list of [sections.named_people, sections.most_seen, sections.other_faces]) {
        const person = (list || []).find((item) => Number(item.id) === Number(id));
        if (person) return person;
    }
    return null;
}

function openPerson(person) {
    if (!person || !person.id) return;
    setScope({
        people: person.id,
        personLabel: cleanPersonLabel(person),
        personThumb: person.face_thumb_url || person.thumb_url || '',
    });
    setActiveLens('grid');
}

function beginRename(card, person) {
    const label = card.querySelector('.person-name');
    label.innerHTML = `<input value="${esc(person.name || '')}" placeholder="${esc(cleanPersonLabel(person))}" aria-label="Person name">`;
    const input = label.querySelector('input');
    input.focus();
    input.select();
    const commit = async () => {
        const next = input.value.trim();
        if (!next) {
            render();
            return;
        }
        const result = await labelPerson(person.id, next);
        if (result && result.ok) {
            person.name = next;
            person.label = next;
            showToast(`Renamed to "${next}"`);
        } else showToast("Couldn't rename person");
        render();
    };
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') commit();
        if (event.key === 'Escape') render();
    });
    input.addEventListener('blur', commit, { once: true });
}

async function ignore(card, person) {
    card.classList.add('is-pending');
    const result = await ignorePerson(person.id);
    if (result && result.ok) {
        showToast('Person ignored');
        peopleData = null;
        load();
    } else {
        card.classList.remove('is-pending');
        showToast("Couldn't ignore person");
    }
}

async function handleReview(button, card) {
    const action = button.dataset.act;
    const suggestionId = Number(card.dataset.suggestionId);
    const sourceId = Number(card.dataset.sourceId);
    const targetId = Number(card.dataset.targetId);
    card.classList.add('is-pending');
    const result = action === 'merge'
        ? await mergePeople(sourceId, targetId)
        : await rejectMergeSuggestion(suggestionId);
    if (result && result.ok) {
        showToast(action === 'merge' ? 'People merged' : 'Suggestion rejected');
        peopleData = null;
        load();
    } else {
        card.classList.remove('is-pending');
        showToast(action === 'merge' ? "Couldn't merge people" : "Couldn't reject suggestion");
    }
}

export function initPeople() {
    if (initialized) return;
    initialized = true;
    document.getElementById('people-flow').addEventListener('click', (event) => {
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
        const action = event.target.closest('button[data-act]')?.dataset.act;
        if (action === 'rename') beginRename(card, person);
        else if (action === 'ignore') ignore(card, person);
        else openPerson(person);
    });
    document.getElementById('people-flow').addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        const card = event.target.closest('.person-card[data-person-id]');
        if (card) openPerson(findPerson(card.dataset.personId));
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
}
