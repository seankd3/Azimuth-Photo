import { escapeHtml } from '../ui.js';

export const DEEP_SEARCH_MAX_TERMS = 200;
export const DEEP_SEARCH_MAX_TERM_LENGTH = 160;
export const DEEP_SEARCH_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
export const DEEP_SEARCH_DAY_LABELS = {
    mon: 'Mon',
    tue: 'Tue',
    wed: 'Wed',
    thu: 'Thu',
    fri: 'Fri',
    sat: 'Sat',
    sun: 'Sun',
};


export function normalizeDeepSearchTerms(value) {
    const rawTerms = Array.isArray(value) ? value : String(value ?? '').split(/\r?\n/);
    const terms = [];
    const seen = new Set();
    for (const item of rawTerms) {
        let term = String(item ?? '').replace(/\s+/g, ' ').trim();
        if (!term) continue;
        if (term.length > DEEP_SEARCH_MAX_TERM_LENGTH) {
            term = term.slice(0, DEEP_SEARCH_MAX_TERM_LENGTH).trim();
        }
        if (!term) continue;
        const key = term.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        terms.push(term);
        if (terms.length >= DEEP_SEARCH_MAX_TERMS) break;
    }
    return terms;
}


export function formatDeepSearchTerms(value) {
    return normalizeDeepSearchTerms(value).join('\n');
}


export function deepSearchTermListHtml(value) {
    const terms = normalizeDeepSearchTerms(value);
    if (!terms.length) {
        return '<div class="catalog-empty">No deep searches queued.</div>';
    }
    const countLabel = `${terms.length.toLocaleString()} ${terms.length === 1 ? 'search' : 'searches'} queued`;
    return `
            <div class="deep-search-term-summary">${countLabel}</div>
            <div class="deep-search-term-chips">
                ${terms.map((term) => `<span class="deep-search-term">${escapeHtml(term)}</span>`).join('')}
            </div>
        `;
}


export function normalizeDeepSearchDays(value) {
    const rawDays = Array.isArray(value)
        ? value
        : String(value ?? '').replace(/,/g, ' ').split(/\s+/);
    const aliases = {
        monday: 'mon',
        mon: 'mon',
        tuesday: 'tue',
        tue: 'tue',
        tues: 'tue',
        wednesday: 'wed',
        wed: 'wed',
        thursday: 'thu',
        thu: 'thu',
        thur: 'thu',
        thurs: 'thu',
        friday: 'fri',
        fri: 'fri',
        saturday: 'sat',
        sat: 'sat',
        sunday: 'sun',
        sun: 'sun',
    };
    const selected = new Set();
    for (const value of rawDays) {
        const day = aliases[String(value ?? '').trim().toLowerCase()];
        if (day) selected.add(day);
    }
    return DEEP_SEARCH_DAYS.filter((day) => selected.has(day));
}


export function formatDeepSearchTime(value) {
    const [hourText, minuteText] = String(value || '').split(':');
    const hour = Number(hourText);
    const minute = Number(minuteText);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return value || '';
    const period = hour >= 12 ? 'PM' : 'AM';
    const displayHour = ((hour + 11) % 12) + 1;
    return `${displayHour}:${String(minute).padStart(2, '0')} ${period}`;
}


export function deepSearchScheduleSummaryText({
    enabled = false,
    days = [],
    start = '07:00',
    end = '16:00',
    timezone = 'America/Chicago',
} = {}) {
    const selectedDays = normalizeDeepSearchDays(days);
    const timezoneLabel = timezone === 'America/Chicago' ? 'Central time' : timezone;
    if (!enabled) {
        return 'Scheduled deep search work is off.';
    }
    if (!selectedDays.length) {
        return 'Schedule is on, but no weekdays are selected.';
    }
    return `Runs ${selectedDays.map((day) => DEEP_SEARCH_DAY_LABELS[day]).join(', ')}, ${formatDeepSearchTime(start)}-${formatDeepSearchTime(end)} ${timezoneLabel}.`;
}
