import { forgetPeopleLabelDraft } from './page.js';


export async function labelPerson(personId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    const input = document.getElementById(`person-label-${personId}`);
    const name = (input?.value || '').trim();
    if (!name) {
        input?.focus();
        return;
    }
    try {
        const res = await fetchImpl(`/api/people/${personId}/label`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'Label failed');
        forgetPeopleLabelDraft(personId);
        if (input) {
            input.value = name;
            input.dataset.serverName = name;
        }
        await loadPeople?.();
        showToast?.('Person labeled');
    } catch (err) {
        showToast?.(err.message || 'Label failed');
    }
}


export async function mergePeople(sourcePersonId, targetPersonId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    try {
        const res = await fetchImpl('/api/people/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_person_id: sourcePersonId, target_person_id: targetPersonId }),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'Merge failed');
        await loadPeople?.();
        showToast?.('People merged');
    } catch (err) {
        showToast?.(err.message || 'Merge failed');
    }
}


export async function rejectPeopleMerge(suggestionId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    try {
        await fetchImpl(`/api/people/merge-suggestions/${suggestionId}/reject`, { method: 'POST' });
        await loadPeople?.();
        showToast?.('Merge rejected');
    } catch {
        showToast?.('Reject failed');
    }
}


export async function ignorePerson(personId, { loadPeople, showToast, fetchImpl = fetch } = {}) {
    try {
        await fetchImpl(`/api/people/${personId}/ignore`, { method: 'POST' });
        await loadPeople?.();
        showToast?.('Person ignored');
    } catch {
        showToast?.('Ignore failed');
    }
}


export function filterLibraryByPerson(personId, { location = globalThis.window?.location || globalThis.location } = {}) {
    const params = new URLSearchParams();
    params.set('people', String(personId));
    location.href = `/library?${params.toString()}`;
}
