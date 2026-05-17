import {
    batchExport as batchExportCore,
    batchFlag as batchFlagCore,
    clearBatchSelection as clearBatchSelectionCore,
    handleCardClick as handleCardClickCore,
    toggleBatchMode as toggleBatchModeCore,
    updateBatchBar as updateBatchBarCore,
} from './batch.js';


export function createBatchSelectionController({
    documentImpl = document,
    getImages = () => [],
    openImage,
    showToast,
    updateImageFlagLocal,
} = {}) {
    let batchMode = false;
    const batchSelected = new Set();
    let lastClickedIndex = -1;

    const api = {
        isBatchMode: () => batchMode,
        isSelected: (imageId) => batchSelected.has(imageId),
        hasSelection: () => batchSelected.size > 0,
        selectedImageIds: () => Array.from(batchSelected),
        updateBatchBar: () => updateBatchBarCore(batchSelected.size, { documentImpl }),
        toggleBatchMode: () => {
            const result = toggleBatchModeCore({
                batchMode,
                batchSelected,
                documentImpl,
            });
            if (result.action === 'clear') {
                api.clearBatchSelection();
                return;
            }
            if (typeof result.batchMode === 'boolean') batchMode = result.batchMode;
            if (result.updateBatchBar) api.updateBatchBar();
        },
        clearBatchSelection: () => {
            const result = clearBatchSelectionCore(batchSelected, { documentImpl });
            batchMode = result.batchMode;
            lastClickedIndex = result.lastClickedIndex;
            api.updateBatchBar();
        },
        handleCardClick: (event, img, card, index) => {
            const result = handleCardClickCore(event, img, card, index, {
                batchMode,
                batchSelected,
                documentImpl,
                images: getImages(),
                lastClickedIndex,
            });
            if (result.action === 'clear') {
                api.clearBatchSelection();
                return;
            }
            if (typeof result.batchMode === 'boolean') batchMode = result.batchMode;
            if (typeof result.lastClickedIndex === 'number') {
                lastClickedIndex = result.lastClickedIndex;
            }
            if (result.updateBatchBar) api.updateBatchBar();
            if (result.action === 'open') openImage?.(result.image);
        },
        batchFlag: async (flag) => {
            await batchFlagCore(flag, {
                imageIds: api.selectedImageIds(),
                images: getImages(),
                updateImageFlagLocal,
                clearBatchSelection: api.clearBatchSelection,
                showToast,
            });
        },
        batchExport: (format) => {
            batchExportCore(format, { imageIds: api.selectedImageIds() });
        },
    };

    return api;
}
