import { createComparePageController } from '../compare/page_controller.js';

export function createLegacyComparePageBridge({
    documentImpl,
    windowImpl,
    setTimeoutImpl,
    getMosaicSize,
    initBottomBarMeasurement,
    startAIStatusPolling,
    handleCompareKey,
    scheduleMosaicRender,
    submitComparison,
    restoreFilters,
    restoreSearchState,
    initSearchInputControls,
    setCompareMode,
    loadFolderList,
    scheduleFilterOptionsLoad,
    initStarHover,
    createComparePageControllerImpl = createComparePageController,
} = {}) {
    const controller = createComparePageControllerImpl({
        documentImpl,
        windowImpl,
        setTimeoutImpl,
        getMosaicSize,
        initBottomBarMeasurement,
        startAIStatusPolling,
        handleCompareKey,
        scheduleMosaicRender,
        submitComparison,
        restoreFilters,
        restoreSearchState,
        initSearchInputControls,
        setCompareMode,
        loadFolderList,
        scheduleFilterOptionsLoad,
        initStarHover,
    });

    return {
        initCompare: (...args) => controller.initCompare(...args),
    };
}
