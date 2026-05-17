import {
    DEEP_SEARCH_DAYS,
    deepSearchScheduleSummaryText,
    deepSearchTermListHtml,
    formatDeepSearchTerms,
    normalizeDeepSearchDays,
    normalizeDeepSearchTerms,
} from './deep_search.js';
import {
    effectiveFastModelSettings,
    normalizeBackgroundWorkMode,
    THUMB_OUTPUT_FIELDS,
    thumbnailChangeNoticeText,
    thumbnailOutputChanges as thumbnailOutputChangesCore,
    thumbnailOutputSnapshot,
} from './display.js';
import { renderEmbeddingModelPresets } from './ai_status.js';

let savedThumbnailOutput = null;


function fastIndexFromSettingsPage(settingsPageData) {
    return settingsPageData?.ai_status?.embedding_indexes?.fast || null;
}


export function renderDeepSearchTerms(value) {
    const el = document.getElementById('deep-search-term-list');
    if (!el) return;
    el.innerHTML = deepSearchTermListHtml(value);
}


export function collectDeepSearchScheduleDays() {
    return Array.from(document.querySelectorAll('[data-deep-search-day]:checked'))
        .map((input) => input.dataset.deepSearchDay)
        .filter((day) => DEEP_SEARCH_DAYS.includes(day));
}


export function setDeepSearchScheduleDays(value) {
    const selected = new Set(normalizeDeepSearchDays(value));
    for (const input of document.querySelectorAll('[data-deep-search-day]')) {
        input.checked = selected.has(input.dataset.deepSearchDay);
    }
}


export function renderDeepSearchSchedule(settings = null) {
    const summary = document.getElementById('deep-search-schedule-summary');
    if (!summary) return;
    const enabled = settings
        ? Boolean(settings.deep_search_schedule_enabled)
        : Boolean(document.getElementById('deep_search_schedule_enabled')?.checked);
    const days = settings
        ? normalizeDeepSearchDays(settings.deep_search_schedule_days)
        : collectDeepSearchScheduleDays();
    const start = settings?.deep_search_schedule_start || document.getElementById('deep_search_schedule_start')?.value || '07:00';
    const end = settings?.deep_search_schedule_end || document.getElementById('deep_search_schedule_end')?.value || '16:00';
    const timezone = settings?.deep_search_schedule_timezone || document.getElementById('deep_search_schedule_timezone')?.value || 'America/Chicago';
    summary.textContent = deepSearchScheduleSummaryText({ enabled, days, start, end, timezone });
}


export function setBackgroundWorkMode(value) {
    const mode = normalizeBackgroundWorkMode(value);
    const hidden = document.getElementById('background_work_mode');
    if (hidden) hidden.value = mode;
    for (const option of document.querySelectorAll('[data-work-mode-option]')) {
        option.classList.toggle('selected', option.dataset.workModeOption === mode);
    }
    const radio = document.querySelector(`input[name="background_work_mode_choice"][value="${mode}"]`);
    if (radio) radio.checked = true;
}


export function selectedBackgroundWorkMode() {
    return normalizeBackgroundWorkMode(
        document.querySelector('input[name="background_work_mode_choice"]:checked')?.value ||
        document.getElementById('background_work_mode')?.value
    );
}


export function cacheProfileHintText(profile) {
    if (profile === 'browse_fast') {
        return 'Fastest browsing puts extra space toward previews before originals.';
    }
    if (profile === 'balanced') {
        return 'Balanced divides extra space between high-res previews and originals.';
    }
    return 'Best quality fills grid and loupe previews first, then favors originals when there is room.';
}


export function updateCacheProfileHint() {
    const input = document.getElementById('cache_profile');
    const hint = document.getElementById('cache-profile-hint');
    if (!input || !hint) return;
    hint.textContent = cacheProfileHintText(input.value);
}


export function populateSettingsForm(settings, { fields = [], settingsPageData = null } = {}) {
    const resolvedSettings = effectiveFastModelSettings(settings, fastIndexFromSettingsPage(settingsPageData));
    renderEmbeddingModelPresets(settingsPageData?.embedding_model_presets || []);
    for (const field of fields) {
        if (field === 'deep_search_schedule_days') {
            setDeepSearchScheduleDays(resolvedSettings[field]);
            continue;
        }
        if (field === 'background_work_mode') {
            setBackgroundWorkMode(resolvedSettings[field]);
            continue;
        }
        const input = document.getElementById(field);
        if (!input || resolvedSettings[field] === undefined || resolvedSettings[field] === null) continue;
        if (field === 'deep_search_terms') {
            input.value = formatDeepSearchTerms(resolvedSettings[field]);
            renderDeepSearchTerms(resolvedSettings[field]);
        } else if (input.type === 'checkbox') input.checked = Boolean(resolvedSettings[field]);
        else input.value = resolvedSettings[field];
    }
    renderDeepSearchSchedule(resolvedSettings);
    setThumbnailCachePolicy('keep');
    updateThumbnailChangeNotice();
}


export function rememberThumbnailOutput(settings) {
    savedThumbnailOutput = thumbnailOutputSnapshot(settings);
}


export function currentThumbnailOutput() {
    const values = {};
    for (const field of THUMB_OUTPUT_FIELDS) {
        const input = document.getElementById(field);
        values[field] = Number(input?.value || 0);
    }
    return values;
}


export function thumbnailOutputChanges() {
    return thumbnailOutputChangesCore(savedThumbnailOutput, currentThumbnailOutput());
}


export function setThumbnailCachePolicy(policy) {
    const input = document.querySelector(`input[name="thumbnail_cache_policy"][value="${policy}"]`);
    if (input) input.checked = true;
}


export function selectedThumbnailCachePolicy() {
    return document.querySelector('input[name="thumbnail_cache_policy"]:checked')?.value || 'keep';
}


export function updateThumbnailChangeNotice() {
    const notice = document.getElementById('thumbnail-change-notice');
    const copy = document.getElementById('thumbnail-change-copy');
    if (!notice) return;
    const changes = thumbnailOutputChanges();
    notice.classList.toggle('hidden', changes.length === 0);
    if (copy && changes.length) {
        copy.textContent = thumbnailChangeNoticeText(changes);
    }
}


export function collectSettingsForm({ fields = [], settingsPageData = null } = {}) {
    const payload = {};
    for (const field of fields) {
        if (field === 'deep_search_schedule_days') {
            payload[field] = collectDeepSearchScheduleDays();
            continue;
        }
        if (field === 'background_work_mode') {
            payload[field] = selectedBackgroundWorkMode();
            continue;
        }
        const input = document.getElementById(field);
        if (!input) continue;
        if (field === 'deep_search_terms') payload[field] = normalizeDeepSearchTerms(input.value);
        else if (input.type === 'checkbox') payload[field] = input.checked;
        else if (input.type === 'number') payload[field] = Number(input.value || '0');
        else payload[field] = input.value;
    }
    payload.thumbnail_cache_policy = selectedThumbnailCachePolicy();
    Object.assign(payload, effectiveFastModelSettings(payload, fastIndexFromSettingsPage(settingsPageData)));
    return payload;
}
