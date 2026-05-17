import {
    LOUPE_TIER_RANKS,
    LOUPE_TIER_TIMEOUTS,
} from '../loupe/tiers.js';


function currentPredicate(isCurrentCompareImage) {
    return typeof isCurrentCompareImage === 'function' ? isCurrentCompareImage : () => true;
}


export function renderCompareImage(img, imgEl, side, token, {
    displayedTiers = null,
    isCurrentCompareImage = null,
    upgradeCompareImageImpl = null,
} = {}) {
    if (!imgEl || !img) return false;
    const isCurrent = currentPredicate(isCurrentCompareImage);
    if (displayedTiers) displayedTiers[side] = -1;
    imgEl.alt = img.filename || '';
    imgEl.classList.add('fading');
    imgEl.onload = () => {
        if (isCurrent(token)) imgEl.classList.remove('fading');
    };
    imgEl.onerror = () => {
        if (isCurrent(token)) imgEl.classList.remove('fading');
    };
    imgEl.src = img.thumb_url || `/api/thumb/md/${img.id}`;
    if (displayedTiers) displayedTiers[side] = LOUPE_TIER_RANKS.md;
    if (upgradeCompareImageImpl) upgradeCompareImageImpl(img, imgEl, side, token).catch(() => {});
    return true;
}


export async function upgradeCompareImage(img, imgEl, side, token, {
    getMediaStatus = null,
    isCurrentCompareImage = null,
    adoptCompareTierImpl = null,
} = {}) {
    if (!getMediaStatus || !adoptCompareTierImpl) return false;
    const isCurrent = currentPredicate(isCurrentCompareImage);
    const status = await getMediaStatus(img.id);
    if (!isCurrent(token)) return false;

    const tiers = status?.tiers || {};
    const cachedBest = tiers.full?.cached ? 'full' : tiers.lg?.cached ? 'lg' : tiers.md?.cached ? 'md' : null;
    if (cachedBest) {
        await adoptCompareTierImpl(img, imgEl, side, cachedBest, true, token, 1000);
    }

    for (const tier of ['md', 'lg']) {
        await adoptCompareTierImpl(img, imgEl, side, tier, false, token, LOUPE_TIER_TIMEOUTS[tier]);
        if (!isCurrent(token)) return false;
    }

    if (tiers.full?.cached) {
        await adoptCompareTierImpl(img, imgEl, side, 'full', true, token, 1200);
    }
    return true;
}


export async function adoptCompareTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs, {
    displayedTiers = null,
    loadImageProbeImpl = null,
    loupeTierUrlImpl = null,
    isCurrentCompareImage = null,
} = {}) {
    if (!imgEl || !loadImageProbeImpl || !loupeTierUrlImpl) return false;
    const rank = LOUPE_TIER_RANKS[tier] || 0;
    const currentRank = Number(displayedTiers?.[side] ?? -1);
    if (rank <= currentRank) return false;
    const result = await loadImageProbeImpl(loupeTierUrlImpl(tier, img.id, cachedOnly), {
        priority: rank >= 2 ? 'high' : 'auto',
        timeoutMs,
    });
    const latestRank = Number(displayedTiers?.[side] ?? -1);
    const isCurrent = currentPredicate(isCurrentCompareImage);
    if (!result.ok || !isCurrent(token) || rank <= latestRank) {
        return false;
    }
    if (displayedTiers) displayedTiers[side] = rank;
    imgEl.src = result.url;
    imgEl.classList.remove('fading');
    return true;
}
