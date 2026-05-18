import { mosaicSizeFromThumbHeight } from '../compare/mosaic.js';
import { createThumbnailSizeHandler } from '../thumbnail_size.js';


export function createLegacyThumbnailSizeBridge({
    getMosaicSize,
    setMosaicSize,
    clearWarmups,
    loadMosaicBatch,
    setThumbHeight,
    createThumbnailSizeHandlerImpl = createThumbnailSizeHandler,
    mosaicSizeFromThumbHeightImpl = mosaicSizeFromThumbHeight,
} = {}) {
    const setThumbSize = createThumbnailSizeHandlerImpl({
        getMosaicSize,
        setMosaicSize,
        mosaicSizeFromThumbHeight: mosaicSizeFromThumbHeightImpl,
        clearWarmups,
        loadMosaicBatch,
        setThumbHeight,
    });

    return {
        setThumbSize,
    };
}
