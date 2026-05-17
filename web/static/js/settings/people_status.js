import {
    peopleSettingsModelText,
    peopleWorkerLabel,
} from '../people/labels.js';


export function renderPeopleSettingsStatus(peopleStatus) {
    if (!peopleStatus) return;
    const statusEl = document.getElementById('people-worker-status');
    const modelEl = document.getElementById('people-model-status');
    const licenseEl = document.getElementById('people-model-license');
    if (statusEl) statusEl.textContent = peopleWorkerLabel(peopleStatus);
    if (modelEl) {
        modelEl.textContent = peopleSettingsModelText(peopleStatus);
    }
    if (licenseEl) licenseEl.textContent = peopleStatus.model_license || '';
}
