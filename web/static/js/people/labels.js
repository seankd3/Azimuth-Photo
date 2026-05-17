export function peopleWorkerLabel(status) {
    const worker = status?.worker || {};
    const state = String(worker.state || 'idle').replace(/_/g, ' ');
    const batch = Number(worker.last_batch_size || 0);
    const pending = Number(worker.pending_cached_images || status?.counts?.pending_cached_images || 0);
    const faces = Number(worker.session_detected_faces || 0);
    const scanned = Number(worker.session_scanned_images || 0);
    const queued = pending > 0 ? ` · ${pending.toLocaleString()} queued` : '';
    const tail = scanned > 0
        ? ` · ${scanned.toLocaleString()} scanned · ${faces.toLocaleString()} faces`
        : '';
    return `${state}${queued}${batch ? ` · last batch ${batch}` : ''}${tail}`;
}


export function isGeneratedPersonLabel(value) {
    return /^Person\s+\d+$/i.test(String(value || '').trim());
}


export function visiblePersonLabel(person, fallback = '') {
    const name = String(person?.name || '').trim();
    if (name) return name;
    const label = String(person?.label || '').trim();
    if (label && !isGeneratedPersonLabel(label)) return label;
    return fallback;
}


export function peopleSettingsModelText(status = {}) {
    const counts = status.counts || {};
    return `${status.model_id || 'buffalo_l'} · ${Number(counts.people || 0).toLocaleString()} people · ${Number(counts.detected_faces || 0).toLocaleString()} faces`;
}
