import { mosaicThumbHeightForSize } from './mosaic.js';
import { bindSharedBottomBarControls } from '../bottom_bar_controls.js';


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
    setMosaicStrategy = () => {},
    mosaicShuffle = () => {},
    loadFolderList = () => {},
    scheduleFilterOptionsLoad = () => {},
    initStarHover = () => {},
    clearSearch = () => {},
    setFilter = () => {},
    toggleMetadataFilters = () => {},
    toggleFilter = () => {},
    toggleStar = () => {},
    setThumbSize = () => {},
    toggleBackgroundWorkPanel = () => {},
    bindSharedBottomBarControlsImpl = bindSharedBottomBarControls,
} = {}) {
    function bindCompareToolbarControls() {
        documentImpl.querySelectorAll('[data-mosaic-strategy]').forEach((btn) => {
            if (btn.dataset.paMosaicStrategyBound === '1') return;
            btn.dataset.paMosaicStrategyBound = '1';
            btn.addEventListener('click', (event) => {
                event.preventDefault();
                setMosaicStrategy(btn.dataset.mosaicStrategy);
            });
        });

        const shuffle = documentImpl.querySelector('[data-action="mosaic-shuffle"]');
        if (shuffle && shuffle.dataset.paMosaicShuffleBound !== '1') {
            shuffle.dataset.paMosaicShuffleBound = '1';
            shuffle.addEventListener('click', (event) => {
                event.preventDefault();
                mosaicShuffle();
            });
        }
    }

    async function initCompare() {
        initBottomBarMeasurement();
        startAIStatusPolling(750, { immediate: true });
        documentImpl.addEventListener('keydown', handleCompareKey);
        windowImpl.addEventListener('resize', scheduleMosaicRender);
        documentImpl.getElementById('compare-left')?.addEventListener('click', () => submitComparison('left'));
        documentImpl.getElementById('compare-right')?.addEventListener('click', () => submitComparison('right'));

        const slider = documentImpl.getElementById('thumb-size');
        if (slider) {
            slider.value = mosaicThumbHeightForSize(getMosaicSize());
        }

        restoreFilters();
        restoreSearchState();
        initSearchInputControls();
        bindSharedBottomBarControlsImpl({
            documentImpl,
            clearSearch,
            setFilter,
            toggleMetadataFilters,
            toggleFilter,
            toggleStar,
            setThumbSize,
            toggleBackgroundWorkPanel,
        });
        bindCompareToolbarControls();
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
