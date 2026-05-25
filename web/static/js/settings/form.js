import {
    effectiveFastModelSettings,
    THUMB_OUTPUT_FIELDS,
    thumbnailChangeNoticeText,
    thumbnailOutputChanges as thumbnailOutputChangesCore,
    thumbnailOutputSnapshot,
} from './display.js';
import { renderEmbeddingModelPresets } from './ai_status.js';

let savedThumbnailOutput = null;


function activeIndexFromSettingsPage(settingsPageData) {
    return settingsPageData?.ai_status?.embedding_index || null;
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
    const resolvedSettings = effectiveFastModelSettings(settings, activeIndexFromSettingsPage(settingsPageData));
    renderEmbeddingModelPresets(settingsPageData?.embedding_model_presets || []);
    for (const field of fields) {
        const input = document.getElementById(field);
        if (!input || resolvedSettings[field] === undefined || resolvedSettings[field] === null) continue;
        if (input.type === 'checkbox') input.checked = Boolean(resolvedSettings[field]);
        else input.value = resolvedSettings[field];
    }
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
        const input = document.getElementById(field);
        if (!input) continue;
        if (input.type === 'checkbox') payload[field] = input.checked;
        else if (input.type === 'number') payload[field] = Number(input.value || '0');
        else payload[field] = input.value;
    }
    payload.thumbnail_cache_policy = selectedThumbnailCachePolicy();
    Object.assign(payload, effectiveFastModelSettings(payload, activeIndexFromSettingsPage(settingsPageData)));
    return payload;
}
