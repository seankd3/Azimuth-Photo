import {
    adoptCompareTier,
    renderCompareImage,
    upgradeCompareImage,
} from './images.js';


export function createCompareImageController({
    displayedTiers,
    getMediaStatus,
    isCurrentCompareImage,
    loadImageProbe,
    loupeTierUrl,
    renderCompareImageImpl = renderCompareImage,
    upgradeCompareImageImpl = upgradeCompareImage,
    adoptCompareTierImpl = adoptCompareTier,
} = {}) {
    async function adoptTier(img, imgEl, side, tier, cachedOnly, token, timeoutMs) {
        return adoptCompareTierImpl(img, imgEl, side, tier, cachedOnly, token, timeoutMs, {
            displayedTiers,
            loadImageProbeImpl: loadImageProbe,
            loupeTierUrlImpl: loupeTierUrl,
            isCurrentCompareImage,
        });
    }

    async function upgradeImage(img, imgEl, side, token) {
        return upgradeCompareImageImpl(img, imgEl, side, token, {
            getMediaStatus,
            isCurrentCompareImage,
            adoptCompareTierImpl: adoptTier,
        });
    }

    function renderImage(img, imgEl, side, token) {
        return renderCompareImageImpl(img, imgEl, side, token, {
            displayedTiers,
            isCurrentCompareImage,
            upgradeCompareImageImpl: upgradeImage,
        });
    }

    return {
        adoptCompareTier: adoptTier,
        renderCompareImage: renderImage,
        upgradeCompareImage: upgradeImage,
    };
}
