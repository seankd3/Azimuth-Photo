import {
    LOUPE_TIER_RANKS,
    LOUPE_TIER_TIMEOUTS,
} from '../loupe/tiers.js';


const MOSAIC_GAP = 3;


export function mosaicGridElo(images = []) {
    if (!images.length) return 0;
    return images.reduce((sum, img) => sum + img.elo, 0) / images.length;
}


export function mosaicThumbHeightForSize(size) {
    const t = (24 - size) / 20;
    return Math.round(120 + t * 280);
}


export function mosaicSizeFromThumbHeight(value) {
    const thumbHeight = Number.parseInt(value, 10);
    const t = (thumbHeight - 120) / (400 - 120);
    return Math.round(24 - t * 20);
}


export function mosaicRowHeightForImages(images = [], {
    containerWidth,
    containerHeight,
    gap = MOSAIC_GAP,
} = {}) {
    let lo = 60;
    let hi = Math.max(60, containerHeight);
    for (let iter = 0; iter < 20; iter++) {
        const mid = (lo + hi) / 2;
        let rows = 1;
        let rowW = 0;
        for (const img of images) {
            const ar = img.aspect_ratio || 1.5;
            const width = mid * ar + gap;
            if (rowW + width > containerWidth + gap && rowW > 0) {
                rows++;
                rowW = width;
            } else {
                rowW += width;
            }
        }
        const totalH = rows * (mid + gap);
        if (totalH > containerHeight) hi = mid;
        else lo = mid;
    }
    return Math.floor(lo);
}


export function renderMosaicGrid({
    images = [],
    grid,
    token,
    onPick = () => {},
    preloadImage = () => {},
    scheduleImageUpgrade = scheduleMosaicImageUpgrade,
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
} = {}) {
    if (!grid || !documentImpl) return { rendered: false, rowHeight: 0 };

    grid.innerHTML = '';
    const viewportW = windowImpl?.innerWidth || 0;
    const viewportH = windowImpl?.innerHeight || 0;
    const containerW = grid.clientWidth || grid.parentElement?.clientWidth || viewportW;
    const barH = documentImpl.querySelector('.bottom-bar')?.offsetHeight || 60;
    const containerH = Math.max(
        80,
        grid.clientHeight || grid.parentElement?.clientHeight || viewportH - barH - 4,
    );
    const rowH = mosaicRowHeightForImages(images, {
        containerWidth: containerW,
        containerHeight: containerH,
    });

    const frag = documentImpl.createDocumentFragment();
    const upgradeJobs = [];
    for (let index = 0; index < images.length; index++) {
        const img = images[index];
        const ar = img.aspect_ratio || 1.5;
        const cell = documentImpl.createElement('div');
        cell.className = 'mosaic-cell skeleton-cell';
        cell.dataset.id = img.id;
        cell.style.height = `${rowH}px`;
        cell.style.flexGrow = ar;
        cell.style.flexBasis = `${rowH * ar}px`;
        cell.onclick = () => onPick(img.id);

        const imageEl = documentImpl.createElement('img');
        imageEl.src = img.thumb_url;
        imageEl.alt = img.filename || '';
        imageEl.dataset.tierRank = '0';
        imageEl.onload = () => {
            imageEl.classList.add('loaded');
            cell.classList.remove('skeleton-cell');
        };
        cell.appendChild(imageEl);
        preloadImage(img.thumb_url);
        frag.appendChild(cell);
        upgradeJobs.push([cell, img, index]);
    }

    grid.appendChild(frag);
    for (const [cell, img, index] of upgradeJobs) {
        scheduleImageUpgrade(cell, img, rowH, token, index);
    }
    return { rendered: true, rowHeight: rowH };
}


export function scheduleMosaicImageUpgrade(cell, img, rowH, token, index = 0, {
    upgradeImage = upgradeMosaicCellImage,
    setTimeoutImpl = globalThis.setTimeout,
} = {}) {
    const delay = Math.min(900, index * 80);
    setTimeoutImpl(() => {
        upgradeImage(cell, img, rowH, token).catch(() => {});
    }, delay);
}


export async function upgradeMosaicCellImage(cell, img, rowH, token, {
    getRenderToken = () => token,
    getMediaStatus = null,
    adoptTier = adoptMosaicTier,
} = {}) {
    if (token !== getRenderToken() || !cell?.isConnected) return false;
    const imgEl = cell.querySelector('img');
    if (!imgEl || !getMediaStatus) return false;

    const status = await getMediaStatus(img.id);
    if (token !== getRenderToken() || !cell.isConnected || cell.dataset.id !== String(img.id)) return false;

    const tiers = status?.tiers || {};
    const cachedBest = tiers.lg?.cached ? 'lg' : tiers.md?.cached ? 'md' : null;
    if (cachedBest) {
        await adoptTier(cell, img, cachedBest, true, token, 900);
    }

    const targetTier = rowH >= 360 ? 'lg' : 'md';
    await adoptTier(cell, img, 'md', false, token, LOUPE_TIER_TIMEOUTS.md);
    if (targetTier === 'lg') {
        await adoptTier(cell, img, 'lg', false, token, LOUPE_TIER_TIMEOUTS.lg);
    }
    return true;
}


export async function adoptMosaicTier(cell, img, tier, cachedOnly, token, timeoutMs, {
    getRenderToken = () => token,
    loadImageProbeImpl = null,
    loupeTierUrlImpl = null,
} = {}) {
    const imgEl = cell?.querySelector('img');
    if (!imgEl || !loadImageProbeImpl || !loupeTierUrlImpl) return false;
    const rank = LOUPE_TIER_RANKS[tier] || 0;
    if (rank <= Number(imgEl.dataset.tierRank || 0)) return false;

    const result = await loadImageProbeImpl(loupeTierUrlImpl(tier, img.id, cachedOnly), {
        priority: 'low',
        timeoutMs,
    });
    if (!result.ok || token !== getRenderToken() || !cell.isConnected || cell.dataset.id !== String(img.id)) {
        return false;
    }
    if (rank <= Number(imgEl.dataset.tierRank || 0)) return false;
    imgEl.dataset.tierRank = String(rank);
    imgEl.src = result.url;
    return true;
}


export function mosaicLoserIds(images = [], winnerId) {
    return images.filter(img => img.id !== winnerId).map(img => img.id);
}


export function mosaicReplacementIndices(age = [], pickedIndex, {
    minimumOldestAge = 10,
} = {}) {
    let oldestIdx = -1;
    let oldestAge = -1;
    for (let i = 0; i < age.length; i++) {
        if (i !== pickedIndex && age[i] > oldestAge) {
            oldestAge = age[i];
            oldestIdx = i;
        }
    }

    const replaceIndices = [pickedIndex];
    if (oldestIdx >= 0 && oldestAge >= minimumOldestAge) replaceIndices.push(oldestIdx);
    return replaceIndices;
}


export function mosaicPickPayload(winnerId, loserIds = []) {
    return {
        winner_id: winnerId,
        loser_ids: loserIds,
    };
}


export async function parseMosaicPickResponse(res) {
    let payload = {};
    try {
        payload = await res.json();
    } catch {}
    if (!res.ok || payload.ok === false) {
        throw new Error(payload.error || 'Failed to save pick');
    }
    return payload;
}


export function postMosaicPick(winnerId, loserIds, {
    fetchImpl = globalThis.fetch,
    setTimeoutImpl = globalThis.setTimeout,
} = {}) {
    return new Promise((resolve) => {
        setTimeoutImpl(() => {
            Promise.resolve()
                .then(() => fetchImpl('/api/mosaic/pick', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(mosaicPickPayload(winnerId, loserIds)),
                }))
                .then(parseMosaicPickResponse)
                .then(
                    (payload) => resolve({ ok: true, payload }),
                    (error) => resolve({ ok: false, error }),
                );
        }, 0);
    });
}
