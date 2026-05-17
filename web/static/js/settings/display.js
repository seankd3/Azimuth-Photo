export const BACKGROUND_WORK_MODES = ['browse', 'balanced', 'max'];
export const BACKGROUND_WORK_MODE_LABELS = {
    browse: 'Browse',
    balanced: 'Light Background',
    max: 'Max Work',
};
export const THUMB_OUTPUT_FIELDS = ['thumb_size_sm', 'thumb_size_md', 'thumb_size_lg', 'thumb_quality'];
export const THUMB_OUTPUT_LABELS = {
    thumb_size_sm: 'small size',
    thumb_size_md: 'medium size',
    thumb_size_lg: 'large size',
    thumb_quality: 'JPEG quality',
};


export function normalizeBackgroundWorkMode(value) {
    const mode = String(value || '').trim().toLowerCase();
    return BACKGROUND_WORK_MODES.includes(mode) ? mode : 'balanced';
}


export function backgroundWorkModeLabel(mode) {
    return BACKGROUND_WORK_MODE_LABELS[mode] || mode;
}


export function cacheProfileLabel(profile) {
    if (profile === 'browse_fast') return 'Fastest browsing';
    if (profile === 'balanced') return 'Balanced';
    return 'Best quality';
}


export function recommendedMemoryGb(settings = {}) {
    const systemRam = Number(settings.system_memory_gb || 0);
    if (systemRam >= 32) return 4;
    if (systemRam >= 16) return 2;
    if (systemRam >= 8) return 1;
    return 0.5;
}


export function formatRatePerMinute(rate) {
    const value = Number(rate || 0);
    if (value <= 0) return 'Waiting for data';
    return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} images/min`;
}


export function formatEta(seconds) {
    const totalSeconds = Math.max(0, Math.round(Number(seconds || 0)));
    if (!totalSeconds) return 'Calculating…';
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    if (hours > 0) return `~${hours}h ${minutes}m`;
    if (minutes > 0) return `~${minutes}m`;
    return `~${totalSeconds}s`;
}


export function badgeStateForIndex(index) {
    if (index?.installing) return { text: 'Installing', cls: 'indexing' };
    if (!index?.installed) return { text: 'Missing', cls: 'missing' };
    const remaining = Number(index.remaining || 0);
    if (remaining <= 0 && Number(index.total_images || 0) > 0) return { text: 'Ready', cls: 'ready' };
    const state = String(index.worker_state || '').replace(/_/g, ' ');
    if (state === 'paused' || state === 'scheduled') return { text: state[0].toUpperCase() + state.slice(1), cls: state };
    if (state === 'error') return { text: 'Error', cls: 'error' };
    return { text: 'Indexing', cls: 'indexing' };
}


export function embeddingIndexDisplay(role, index = {}) {
    const total = Number(index?.total_images || 0);
    const embedded = Number(index?.embedded || 0);
    const remaining = Number(index?.remaining || Math.max(total - embedded, 0));
    const pct = total > 0 ? Math.max(0, Math.min(100, Number(index?.progress_pct || (embedded / total) * 100))) : 0;
    const dimension = Number(index?.dimension || 0);
    const message = index?.install_message || index?.worker_message || '';
    const queryText = role === 'deep'
        ? ` · ${Number(index?.embedded_queries || 0).toLocaleString()} cached queries, ${Number(index?.pending_queries || 0).toLocaleString()} pending`
        : '';
    return {
        modelText: index?.model_id
            ? `${index.model_id}${dimension ? ` · ${dimension.toLocaleString()}d` : ''}`
            : '—',
        badge: badgeStateForIndex(index || {}),
        pct,
        progressText: `${embedded.toLocaleString()} / ${total.toLocaleString()} images · ${pct.toFixed(1)}%`,
        statusText: `${remaining.toLocaleString()} images remaining${queryText}${message ? ` · ${message}` : ''}`,
    };
}


export function effectiveFastModelSettings(settings = {}, fastIndex = null) {
    if (!fastIndex?.model_id) return settings || {};
    return {
        ...(settings || {}),
        embed_model_preset: 'qwen3-vl-embedding-2b',
        embed_model_id: fastIndex.model_id,
        embed_model_revision: 'main',
        embed_model_dir: fastIndex.model_dir,
        embed_model_dim: fastIndex.dimension,
    };
}


export function autoWorkersText(settings = {}) {
    const cpu = Number(settings.cpu_count || 0);
    const ram = settings.system_memory_gb ? `${settings.system_memory_gb} GB system RAM` : 'system RAM unknown';
    return `${Number(settings.user_workers || 0)} request · ${Number(settings.prefetch_workers || 0)} background` +
        (cpu > 0 ? ` on ${cpu} CPU threads` : '') +
        ` · ${ram}`;
}


export function autoPrefetchText(settings = {}) {
    return `scan ${Number(settings.scan_prefetch_limit || 0)} · ` +
        `review ${Number(settings.review_prefetch_limit || 0)} · ` +
        `compare ${Number(settings.compare_prefetch_limit || 0)} · ` +
        `mosaic ${Number(settings.mosaic_prefetch_limit || 0)}`;
}


export function browserCachePolicyText(settings = {}) {
    const maxAge = Number(settings.browser_cache_max_age || 0);
    const stale = Number(settings.browser_cache_stale_while_revalidate || 0);
    return `${maxAge.toLocaleString()}s max-age · ${stale.toLocaleString()}s stale-while-revalidate`;
}


export function thumbnailOutputSnapshot(settings = {}, fields = THUMB_OUTPUT_FIELDS) {
    const values = {};
    for (const field of fields) {
        values[field] = Number(settings?.[field] || 0);
    }
    return values;
}


export function thumbnailOutputChanges(saved, current, fields = THUMB_OUTPUT_FIELDS) {
    if (!saved) return [];
    const changes = [];
    for (const field of fields) {
        if (Number(saved[field] || 0) !== Number(current?.[field] || 0)) {
            changes.push(`${THUMB_OUTPUT_LABELS[field]} ${saved[field]} → ${current?.[field]}`);
        }
    }
    return changes;
}


export function thumbnailChangeNoticeText(changes = []) {
    if (!changes.length) return '';
    return `${changes.join(', ')}. Keeping existing previews avoids extra work. Refreshing them keeps old previews usable while photoArchive replaces each file in the background.`;
}
