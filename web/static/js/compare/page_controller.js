import { mosaicThumbHeightForSize } from './mosaic.js';


export function createComparePageController({
    documentImpl = document,
    windowImpl = window,
    setTimeoutImpl = setTimeout,
    getMosaicSize = () => 12,
    initBottomBarMeasurement = () => {},
    startAIStatusPolling = () => {},
    handleCompareKey = () => {},
    scheduleMosaicRender = () => {},
    submitComparison = () => {},
    restoreFilters = () => {},
    restoreSearchState = () => {},
    initSearchInputControls = () => {},
    setCompareMode = () => {},
    loadFolderList = () => {},
    scheduleFilterOptionsLoad = () => {},
    initStarHover = () => {},
} = {}) {
    async function initCompare() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        documentImpl.addEventListener('keydown', handleCompareKey);
        windowImpl.addEventListener('resize', scheduleMosaicRender);
        documentImpl.getElementById('compare-left').addEventListener('click', () => submitComparison('left'));
        documentImpl.getElementById('compare-right').addEventListener('click', () => submitComparison('right'));

        const slider = documentImpl.getElementById('thumb-size');
        if (slider) {
            slider.value = mosaicThumbHeightForSize(getMosaicSize());
        }

        restoreFilters();
        restoreSearchState();
        initSearchInputControls();
        setCompareMode('mosaic');
        setTimeoutImpl(() => {
            loadFolderList();
            scheduleFilterOptionsLoad();
        }, 500);
        initStarHover();
    }

    return {
        initCompare,
    };
}
