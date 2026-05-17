import {
    initStarHover as initStarHoverCore,
    loadFilterOptions as loadFilterOptionsCore,
    loadFolderList as loadFolderListCore,
    scheduleFilterOptionsLoad as scheduleFilterOptionsLoadCore,
    toggleMetadataFilters as toggleMetadataFiltersCore,
} from '../filters.js';
import {
    clearLibraryFilters as clearLibraryFiltersCore,
    setFilter as setLibraryFilterCore,
    toggleFilter as toggleFilterCore,
    toggleStar as toggleStarCore,
} from './filters.js';


export function createLibraryFilterController({
    documentImpl = document,
    emptyFilters,
    getFilters,
    setFilters,
    clearWarmups,
    resetLibraryResults,
    loadRankings,
    isDateSortActive,
    updateDateScrubber,
    currentLibraryView,
    loadMap,
    getCompareMode,
    loadMosaicBatch,
    resetComparePairs,
    fetchComparePairs,
    showComparePair,
    updateMetadataFilterButton,
    saveFilters,
    activeMetadataFilterCount,
    loadFolderListImpl = loadFolderListCore,
    loadFilterOptionsImpl = loadFilterOptionsCore,
    scheduleFilterOptionsLoadImpl = scheduleFilterOptionsLoadCore,
    toggleMetadataFiltersImpl = toggleMetadataFiltersCore,
    initStarHoverImpl = initStarHoverCore,
} = {}) {
    function reloadForFilters() {
        clearWarmups();
        const grid = documentImpl.getElementById('rankings-grid');
        if (grid) {
            resetLibraryResults({ clearBatch: true });
            loadRankings(true);
            if (isDateSortActive()) updateDateScrubber();
            if (currentLibraryView() === 'map') loadMap();
            return 'library';
        }
        if (getCompareMode() === 'mosaic') {
            loadMosaicBatch();
            return 'mosaic';
        }
        resetComparePairs();
        fetchComparePairs().then(() => showComparePair());
        return 'compare';
    }

    function setFilter(key, value) {
        const nextFilters = setLibraryFilterCore(key, value, {
            filters: getFilters(),
            updateMetadataFilterButton,
            saveFilters,
            reloadForFilters,
        });
        setFilters(nextFilters);
        return nextFilters;
    }

    function clearLibraryFilters() {
        return clearLibraryFiltersCore({
            emptyFilters,
            document: documentImpl,
            setFilters,
            updateMetadataFilterButton,
            saveFilters,
            reloadForFilters,
        });
    }

    function toggleFilter(key, value, btn) {
        const nextFilters = toggleFilterCore(key, value, btn, {
            filters: getFilters(),
            saveFilters,
            reloadForFilters,
        });
        setFilters(nextFilters);
        return nextFilters;
    }

    function toggleStar(level) {
        const nextFilters = toggleStarCore(level, {
            filters: getFilters(),
            document: documentImpl,
            saveFilters,
            reloadForFilters,
        });
        setFilters(nextFilters);
        return nextFilters;
    }

    function loadFolderList() {
        return loadFolderListImpl({ filters: getFilters() });
    }

    function loadFilterOptions() {
        return loadFilterOptionsImpl({
            filters: getFilters(),
            updateMetadataFilterButton,
        });
    }

    function scheduleFilterOptionsLoad() {
        return scheduleFilterOptionsLoadImpl({
            filters: getFilters(),
            activeMetadataFilterCount,
            loadFilterOptions,
        });
    }

    function toggleMetadataFilters() {
        return toggleMetadataFiltersImpl({ loadFilterOptions });
    }

    function initStarHover() {
        return initStarHoverImpl();
    }

    return {
        clearLibraryFilters,
        initStarHover,
        loadFilterOptions,
        loadFolderList,
        reloadForFilters,
        scheduleFilterOptionsLoad,
        setFilter,
        toggleFilter,
        toggleMetadataFilters,
        toggleStar,
    };
}
