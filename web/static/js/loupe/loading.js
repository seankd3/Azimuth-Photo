import {
    LOUPE_TIER_RANKS,
    LOUPE_TIER_TIMEOUTS,
    loupeTierUrl,
} from './tiers.js';
import { withTimeout } from '../warmup.js';

export function cancelLoupeProbes(
    probes,
    { clearLoupeTierLoadingImpl = () => {} } = {},
) {
    for (const probe of Array.from(probes || [])) {
        probe.cancelled = true;
        if (probe.timer) (probe.clearTimeoutImpl || globalThis.clearTimeout)(probe.timer);
        probe.img.onload = null;
        probe.img.onerror = null;
        probe.img.src = '';
        probes.delete(probe);
        probe.done = true;
        probe.resolveOnce(false);
    }
    clearLoupeTierLoadingImpl();
}

export function loadLoupeTier(
    img,
    url,
    rank,
    token,
    {
        adoptDimensions = false,
        timeoutMs = 0,
        probes = new Set(),
        ImageImpl = globalThis.Image,
        documentImpl = globalThis.document,
        setTimeoutImpl = globalThis.setTimeout,
        clearTimeoutImpl = globalThis.clearTimeout,
        isCurrentLoupeImage = () => false,
        getDisplayedTierRank = () => -1,
        setDisplayedTierRank = () => {},
        setFullLoadToken = () => {},
        setLoupeTierLoading = () => {},
        clearLoupeTierLoading = () => {},
        renderLoupeStatusLine = () => {},
        refreshLoupeMediaStatus = () => {},
        adoptSourceDimensions = () => {},
    } = {},
) {
    if (!url) return Promise.resolve(false);
    return new Promise((resolve) => {
        const probeImg = new ImageImpl();
        const probe = {
            img: probeImg,
            timer: null,
            clearTimeoutImpl,
            resolved: false,
            resolveOnce(value) {
                if (probe.resolved) return;
                probe.resolved = true;
                resolve(value);
            },
            done: false,
            cancelled: false,
        };
        probes.add(probe);

        const cleanup = () => {
            if (probe.done) return;
            probe.done = true;
            if (probe.timer) clearTimeoutImpl(probe.timer);
            probes.delete(probe);
        };

        const finish = (loaded) => {
            cleanup();
            probe.resolveOnce(loaded);
        };

        probeImg.decoding = 'async';
        if ('fetchPriority' in probeImg) probeImg.fetchPriority = rank >= 2 ? 'high' : 'auto';
        if (rank > LOUPE_TIER_RANKS.sm && rank > getDisplayedTierRank() && isCurrentLoupeImage(img, token)) {
            setLoupeTierLoading(rank);
        }
        probeImg.onload = () => {
            if (probe.cancelled) {
                finish(false);
                return;
            }
            if (!probeImg.naturalWidth || !probeImg.naturalHeight) {
                finish(false);
                return;
            }
            const isCurrent = isCurrentLoupeImage(img, token);
            if (isCurrent && rank > getDisplayedTierRank()) {
                if (adoptDimensions && probeImg.naturalWidth > 0 && probeImg.naturalHeight > 0) {
                    adoptSourceDimensions(probeImg.naturalWidth, probeImg.naturalHeight);
                }

                const loupeImg = documentImpl?.getElementById?.('loupe-img');
                if (loupeImg) {
                    setDisplayedTierRank(rank);
                    if (rank === LOUPE_TIER_RANKS.full) setFullLoadToken(token);
                    loupeImg.src = probeImg.src;
                    loupeImg.style.opacity = '1';
                    clearLoupeTierLoading(rank);
                    renderLoupeStatusLine();
                }
            }
            if (isCurrent) refreshLoupeMediaStatus(img, token);
            finish(true);
        };
        probeImg.onerror = () => {
            if (isCurrentLoupeImage(img, token)) clearLoupeTierLoading(rank);
            finish(false);
        };
        if (timeoutMs > 0) {
            probe.timer = setTimeoutImpl(() => {
                probe.timer = null;
                if (isCurrentLoupeImage(img, token)) clearLoupeTierLoading(rank);
                probe.resolveOnce(false);
            }, timeoutMs);
        }
        probeImg.src = url;
    });
}

export async function runLoupeProgressiveLoad(
    img,
    token,
    {
        loadLoupeTierImpl = loadLoupeTier,
        getMediaStatus = async () => null,
        withTimeoutImpl = withTimeout,
        applyLoupeMediaStatus = () => false,
        isCurrentLoupeImage = () => false,
        loupeTierUrlImpl = loupeTierUrl,
        getDisplayedTierRank = () => -1,
        setFullLoadToken = () => {},
    } = {},
) {
    const smUrl = img.thumb_url || loupeTierUrlImpl('sm', img.id);
    loadLoupeTierImpl(img, smUrl, LOUPE_TIER_RANKS.sm, token, {
        timeoutMs: LOUPE_TIER_TIMEOUTS.md,
    });

    const statusRequest = getMediaStatus(img.id, { force: true });
    statusRequest.then((latest) => applyLoupeMediaStatus(latest, img, token)).catch(() => {});
    const status = await withTimeoutImpl(statusRequest, 500, null);
    if (!isCurrentLoupeImage(img, token)) return;
    applyLoupeMediaStatus(status, img, token);

    const cachedBest = status?.best_cached;
    if (cachedBest && cachedBest !== 'sm') {
        const rank = LOUPE_TIER_RANKS[cachedBest];
        await loadLoupeTierImpl(img, loupeTierUrlImpl(cachedBest, img.id, true), rank, token, {
            adoptDimensions: cachedBest === 'full',
            timeoutMs: 900,
        });
        if (!isCurrentLoupeImage(img, token)) return;
    }

    const tiers = [
        { name: 'md', adoptDimensions: false },
        { name: 'lg', adoptDimensions: false },
        { name: 'full', adoptDimensions: true },
    ];
    for (const tier of tiers) {
        const rank = LOUPE_TIER_RANKS[tier.name];
        if (rank <= getDisplayedTierRank()) continue;
        if (tier.name === 'full') setFullLoadToken(token);
        const loaded = await loadLoupeTierImpl(img, loupeTierUrlImpl(tier.name, img.id), rank, token, {
            adoptDimensions: tier.adoptDimensions,
            timeoutMs: LOUPE_TIER_TIMEOUTS[tier.name],
        });
        if (tier.name === 'full' && !loaded && isCurrentLoupeImage(img, token) && getDisplayedTierRank() < rank) {
            setFullLoadToken(0);
        }
        if (!isCurrentLoupeImage(img, token)) return;
    }
}
