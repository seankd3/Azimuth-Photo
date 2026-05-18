import { createBatchSelectionController } from '../library/batch_controller.js';

export function createLegacyBatchBridge({
    createBatchSelectionControllerImpl = createBatchSelectionController,
    getImages,
    openImage,
    showToast,
    updateImageFlagLocal,
} = {}) {
    const batchController = createBatchSelectionControllerImpl({
        getImages,
        openImage,
        showToast,
        updateImageFlagLocal,
    });

    return {
        batchExport: (format) => batchController.batchExport(format),
        batchFlag: (flag) => batchController.batchFlag(flag),
        clearBatchSelection: () => batchController.clearBatchSelection(),
        handleCardClick: (event, img, card, index) => batchController.handleCardClick(event, img, card, index),
        hasSelection: () => batchController.hasSelection(),
        isBatchMode: () => batchController.isBatchMode(),
        isSelected: (imageId) => batchController.isSelected(imageId),
        toggleBatchMode: () => batchController.toggleBatchMode(),
    };
}
