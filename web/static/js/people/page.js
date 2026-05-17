import { mergeSuggestionsHtml, peopleGridHtml } from './cards.js';
import { peopleWorkerLabel } from './labels.js';

const peopleLabelDrafts = new Map();
let peoplePoller = null;


export function snapshotPeopleLabelDrafts() {
    const active = document.activeElement;
    let focusedDraft = null;
    document.querySelectorAll('.people-label-row input[id^="person-label-"]').forEach((input) => {
        const personId = Number(String(input.id || '').replace('person-label-', ''));
        if (!personId) return;
        const value = input.value || '';
        const serverName = input.dataset.serverName || '';
        const focused = active === input;
        if (focused || value !== serverName) {
            peopleLabelDrafts.set(personId, value);
        } else {
            peopleLabelDrafts.delete(personId);
        }
        if (focused) {
            focusedDraft = {
                personId,
                start: input.selectionStart,
                end: input.selectionEnd,
            };
        }
    });
    return focusedDraft;
}


export function restorePeopleLabelFocus(focusedDraft) {
    if (!focusedDraft?.personId) return;
    const input = document.getElementById(`person-label-${focusedDraft.personId}`);
    if (!input) return;
    input.focus({ preventScroll: true });
    if (typeof focusedDraft.start === 'number' && typeof focusedDraft.end === 'number') {
        input.setSelectionRange(focusedDraft.start, focusedDraft.end);
    }
}


export function rememberPeopleLabelDraft(personId, value) {
    peopleLabelDrafts.set(Number(personId), String(value || ''));
}


export function forgetPeopleLabelDraft(personId) {
    peopleLabelDrafts.delete(Number(personId));
}


export function useFallbackThumb(img) {
    const fallback = img?.dataset?.fallbackSrc || '';
    if (!fallback) return false;
    img.onerror = null;
    img.dataset.fallbackSrc = '';
    img.src = fallback;
    return true;
}


export function renderPeopleGrid(targetId, people, emptyText) {
    const target = document.getElementById(targetId);
    if (!target) return false;
    target.innerHTML = peopleGridHtml(people, emptyText, peopleLabelDrafts);
    return true;
}


export function renderMergeSuggestions(suggestions) {
    const target = document.getElementById('people-needs-review');
    if (!target) return false;
    target.innerHTML = mergeSuggestionsHtml(suggestions);
    return true;
}


export function renderPeople(data) {
    const focusedDraft = snapshotPeopleLabelDrafts();
    const status = data?.status || {};
    const statusEl = document.getElementById('people-status-line');
    const licenseEl = document.getElementById('people-license-line');
    const counts = data?.counts || status.counts || {};
    if (statusEl) {
        const detected = Number(counts.detected_faces || 0).toLocaleString();
        const people = Number(counts.people || 0).toLocaleString();
        statusEl.textContent = `${peopleWorkerLabel(status)} · ${people} people · ${detected} faces`;
    }
    if (licenseEl) {
        licenseEl.textContent = `${status.model_id || 'buffalo_l'} · ${status.model_dir || ''} · ${status.model_license || ''}`;
    }
    const sections = data?.sections || {};
    renderPeopleGrid('people-most-seen', sections.most_seen, 'No frequent unknown faces yet.');
    renderPeopleGrid('people-named', sections.named_people, 'No named people yet.');
    renderMergeSuggestions(sections.needs_review);
    renderPeopleGrid('people-other', sections.other_faces, 'No long-tail faces yet.');
    const otherSummary = document.getElementById('people-other-summary');
    if (otherSummary) {
        otherSummary.textContent = `Other Faces (${Number(counts.other_faces || 0).toLocaleString()})`;
    }
    restorePeopleLabelFocus(focusedDraft);
}


export async function loadPeople({ fetchImpl = fetch } = {}) {
    const statusEl = document.getElementById('people-status-line');
    if (statusEl && !document.querySelector('.people-label-row input:focus')) {
        statusEl.textContent = 'Loading People...';
    }
    try {
        const res = await fetchImpl('/api/people?limit=48');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'People load failed');
        renderPeople(data);
    } catch (err) {
        if (statusEl) statusEl.textContent = `People unavailable: ${err.message}`;
    }
}


export function initPeople({
    loadPeopleImpl = loadPeople,
    setIntervalImpl = setInterval,
    clearIntervalImpl = clearInterval,
    intervalMs = 10000,
} = {}) {
    loadPeopleImpl();
    if (peoplePoller) clearIntervalImpl(peoplePoller);
    peoplePoller = setIntervalImpl(() => {
        if (!document.hidden) loadPeopleImpl();
    }, intervalMs);
}
