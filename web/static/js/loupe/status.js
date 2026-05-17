import {
    LOUPE_TIER_NAMES,
    LOUPE_TIER_RANKS,
} from './tiers.js';


export function loupeCacheMark(status, tier) {
    const tierStatus = status?.tiers?.[tier];
    if (!tierStatus) return 'x';
    return tierStatus.cached ? '✓' : 'x';
}


export function loupeViewingTierName(displayedTierRank) {
    return LOUPE_TIER_NAMES[displayedTierRank] || 'none';
}


export function loupeCacheStatusText(status, displayedTierRank) {
    return [
        'ssd:',
        `sm ${loupeCacheMark(status, 'sm')}`,
        `md ${loupeCacheMark(status, 'md')}`,
        `lg ${loupeCacheMark(status, 'lg')}`,
        `original ${loupeCacheMark(status, 'full')}`,
        `· viewing ${loupeViewingTierName(displayedTierRank)}`,
    ].join(' ');
}


export function loupeTierLoadingText(rank) {
    return rank >= LOUPE_TIER_RANKS.full ? 'Loading Original...' : 'Loading HD...';
}


export function setLoupeTierLoading({
    documentImpl = globalThis.document,
    displayedTierRank = -1,
    rank = -1,
} = {}) {
    const tierEl = documentImpl?.getElementById?.('loupe-overlay-tier');
    if (!tierEl || rank <= displayedTierRank) return false;
    tierEl.classList.add('loupe-tier-loading');
    tierEl.textContent = loupeTierLoadingText(rank);
    return true;
}


export function clearLoupeTierLoading({
    documentImpl = globalThis.document,
    loadingTierRank = -1,
    rank = loadingTierRank,
} = {}) {
    if (rank !== loadingTierRank) return false;
    const tierEl = documentImpl?.getElementById?.('loupe-overlay-tier');
    if (tierEl) tierEl.classList.remove('loupe-tier-loading');
    return true;
}


export function loupeFlagLabel(flag) {
    if (flag === 'picked') return 'Picked';
    if (flag === 'rejected') return 'Rejected';
    return 'Unflagged';
}


export function renderLoupeStatusLine({
    documentImpl = document,
    displayedTierRank = -1,
    flag = 'unflagged',
    loadingTierRank = -1,
    mediaStatus = null,
    showCacheStatus = true,
} = {}) {
    const tierEl = documentImpl.getElementById('loupe-overlay-tier');
    if (!tierEl) return '';
    if (loadingTierRank > displayedTierRank) return tierEl.textContent || '';
    tierEl.classList.remove('loupe-tier-loading');
    const tier = LOUPE_TIER_NAMES[displayedTierRank] || 'Image';
    const label = loupeFlagLabel(flag);
    const text = showCacheStatus
        ? `${loupeCacheStatusText(mediaStatus, displayedTierRank)} · ${label}`
        : `${tier} · ${label}`;
    tierEl.textContent = text;
    return text;
}
