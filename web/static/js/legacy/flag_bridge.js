import {
    setCurrentLibraryFlag as setCurrentLibraryFlagCore,
    setImageFlag as setImageFlagCore,
    updateImageFlagLocal as updateImageFlagLocalCore,
} from '../library/flags.js';

export function createLegacyFlagBridge({
    setCurrentLibraryFlagImpl = setCurrentLibraryFlagCore,
    setImageFlagImpl = setImageFlagCore,
    updateImageFlagLocalImpl = updateImageFlagLocalCore,
    getImages = () => [],
    getLightboxIndex = () => -1,
    getSelectedLibraryIndex = () => -1,
    getStandaloneImage = () => null,
    getCurrentImage = () => null,
    showToast,
    updateLoupeFlagDisplay,
} = {}) {
    const bridge = {
        updateImageFlagLocal: (imageId, flag) => updateImageFlagLocalImpl(imageId, flag, {
            images: getImages(),
            loupeStandaloneImage: getStandaloneImage(),
            loupeCurrentImage: getCurrentImage(),
            lightboxIndex: getLightboxIndex(),
            updateLoupeFlagDisplay,
        }),
        setImageFlag: async (imageId, flag) => setImageFlagImpl(imageId, flag, {
            images: getImages(),
            showToast,
            updateImageFlagLocalImpl: bridge.updateImageFlagLocal,
        }),
        setCurrentLibraryFlag: (flag) => setCurrentLibraryFlagImpl(flag, {
            images: getImages(),
            lightboxIndex: getLightboxIndex(),
            loupeStandaloneImage: getStandaloneImage(),
            selectedLibraryIndex: getSelectedLibraryIndex(),
            setImageFlagImpl: bridge.setImageFlag,
        }),
    };

    return bridge;
}
