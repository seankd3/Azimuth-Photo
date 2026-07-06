import { forgetPeopleLabelDraft } from './page.js';

let labelInFlight = false;
let mergeInFlight = false;
let ignoreInFlight = false;


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export async function labelPerson(personId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    const input = document.getElementById(`person-label-${personId}`);
    const name = (input?.value || '').trim();
    if (!name) {
        input?.focus();
        return;
    }
    if (labelInFlight) return;
    labelInFlight = true;
    try {
        const res = await fetchImpl(`/api/people/${personId}/label`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        await responseDataOrError(res, 'Label failed');
        forgetPeopleLabelDraft(personId);
        if (input) {
            input.value = name;
            input.dataset.serverName = name;
        }
        await loadPeople?.({ force: true });
        showToast?.('Person labeled');
    } catch (err) {
        showToast?.(err.message || 'Label failed');
    } finally {
        labelInFlight = false;
    }
}


export async function mergePeople(sourcePersonId, targetPersonId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    if (mergeInFlight) return;
    mergeInFlight = true;
    try {
        const res = await fetchImpl('/api/people/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_person_id: sourcePersonId, target_person_id: targetPersonId }),
        });
        await responseDataOrError(res, 'Merge failed');
        await loadPeople?.({ force: true });
        showToast?.('People merged');
    } catch (err) {
        showToast?.(err.message || 'Merge failed');
    } finally {
        mergeInFlight = false;
    }
}


export async function rejectPeopleMerge(suggestionId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    try {
        const res = await fetchImpl(`/api/people/merge-suggestions/${suggestionId}/reject`, { method: 'POST' });
        await responseDataOrError(res, 'Reject failed');
        await loadPeople?.({ force: true });
        showToast?.('Merge rejected');
    } catch (err) {
        showToast?.(err.message || 'Reject failed');
    }
}


export async function ignorePerson(personId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    if (ignoreInFlight) return;
    ignoreInFlight = true;
    try {
        const res = await fetchImpl(`/api/people/${personId}/ignore`, { method: 'POST' });
        await responseDataOrError(res, 'Ignore failed');
        await loadPeople?.({ force: true });
        showToast?.('Person ignored');
    } catch (err) {
        showToast?.(err.message || 'Ignore failed');
    } finally {
        ignoreInFlight = false;
    }
}


export function filterLibraryByPerson(personId, { location = globalThis.window?.location || globalThis.location } = {}) {
    const params = new URLSearchParams();
    params.set('people', String(personId));
    location.href = `/library?${params.toString()}`;
}
