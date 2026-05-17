import {
    loupeHotSetTierIds,
    loupeNeighborOffsets,
} from './navigation.js';
import {
    LOUPE_TIER_TIMEOUTS,
    loupeTierUrl,
} from './tiers.js';


export function createLoupeWarmupController({
    getLibraryImages,
    getLightboxIndex,
    getLoupeImageToken,
    getWarmupGeneration,
    getCurrentLoupeImage,
    isCurrentLoupeImage,
    enqueueWarmup,
    warmImageTiers,
    preloadImageWithTimeout,
    getMediaStatus,
    loupeTierUrlImpl = loupeTierUrl,
    tierTimeouts = LOUPE_TIER_TIMEOUTS,
    preloadRadius = 3,
    loupeNeighborOffsetsImpl = loupeNeighborOffsets,
    loupeHotSetTierIdsImpl = loupeHotSetTierIds,
} = {}) {
    function isWarmCurrent(token, generation) {
        const current = getCurrentLoupeImage();
        return generation === getWarmupGeneration()
            && Boolean(current)
            && isCurrentLoupeImage(current, token);
    }

    function preloadLoupeNeighbors(direction = 0) {
        const lightboxIndex = getLightboxIndex();
        if (lightboxIndex < 0) return false;
        const images = getLibraryImages();
        const token = getLoupeImageToken();
        const generation = getWarmupGeneration();
        for (const offset of loupeNeighborOffsetsImpl(preloadRadius, direction)) {
            const ni = lightboxIndex + offset;
            if (ni < 0 || ni >= images.length) continue;
            const neighbor = images[ni];
            const distance = Math.abs(offset);
            enqueueWarmup(async () => {
                if (!isWarmCurrent(token, generation)) return;
                await preloadLoupeNeighbor(neighbor, distance, token, generation);
            }, { generation });
        }
        return true;
    }

    function warmLoupeHotSet(direction = 0) {
        const tiers = loupeHotSetTierIdsImpl(getLibraryImages(), getLightboxIndex(), direction);
        if (tiers) warmImageTiers(tiers);
        return tiers;
    }

    async function preloadLoupeNeighbor(
        img,
        distance = 1,
        token = getLoupeImageToken(),
        generation = getWarmupGeneration(),
    ) {
        if (!isWarmCurrent(token, generation)) return false;
        await preloadImageWithTimeout(img.thumb_url, 'low', 1200);
        if (!isWarmCurrent(token, generation)) return false;
        const status = await getMediaStatus(img.id);
        if (!isWarmCurrent(token, generation)) return false;
        const tiers = status?.tiers || {};
        if (tiers.md?.cached) {
            await preloadImageWithTimeout(tiers.md.cached_url, 'low', tierTimeouts.md);
        }
        if (!isWarmCurrent(token, generation)) return false;
        if (tiers.lg?.cached) {
            await preloadImageWithTimeout(tiers.lg.cached_url, 'low', tierTimeouts.lg);
        }
        if (!isWarmCurrent(token, generation)) return false;
        if (distance === 1) {
            if (!tiers.md?.cached) {
                await preloadImageWithTimeout(loupeTierUrlImpl('md', img.id), 'low', tierTimeouts.md);
            }
            if (!isWarmCurrent(token, generation)) return false;
            if (!tiers.lg?.cached) {
                await preloadImageWithTimeout(loupeTierUrlImpl('lg', img.id), 'low', tierTimeouts.lg);
            }
            if (!isWarmCurrent(token, generation)) return false;
            await preloadImageWithTimeout(
                tiers.full?.cached ? tiers.full.cached_url : loupeTierUrlImpl('full', img.id),
                'low',
                tierTimeouts.full,
            );
        }
        return true;
    }

    return {
        preloadLoupeNeighbor,
        preloadLoupeNeighbors,
        warmLoupeHotSet,
    };
}
