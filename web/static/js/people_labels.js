const FAKE_PERSON_RE = /^person\s+\d+$/i;

export function personLabel(person, fallback = '') {
    const explicit = String(person?.name || '').trim();
    if (explicit) return explicit;
    for (const key of ['label', 'display_name', 'person_label']) {
        const value = String(person?.[key] || '').trim();
        if (value && !FAKE_PERSON_RE.test(value)) return value;
    }
    const cleanFallback = String(fallback || '').trim();
    return cleanFallback && !FAKE_PERSON_RE.test(cleanFallback) ? cleanFallback : 'Unnamed';
}

export function isUnnamedPersonLabel(label) {
    return String(label || '').trim() === 'Unnamed';
}
