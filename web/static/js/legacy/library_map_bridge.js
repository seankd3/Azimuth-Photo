import { createLibraryMapController } from '../library/map_controller.js';


export function createLegacyLibraryMapBridge({
    documentImpl = globalThis.document,
    windowImpl = globalThis.window,
    fetchImpl = globalThis.fetch,
    consoleImpl = globalThis.console,
    clearWarmups,
    clearBatchSelection,
    currentFilterState,
    currentQueryState,
    getLibraryImages,
    libraryScrollRoot,
    openLightbox,
    openStandaloneLightbox,
    showToast,
    syncDateScrubberVisibility,
    createLibraryMapControllerImpl = createLibraryMapController,
} = {}) {
    const mapController = createLibraryMapControllerImpl({
        documentImpl,
        windowImpl,
        fetchImpl,
        consoleImpl,
        clearWarmups,
        clearBatchSelection,
        currentFilterState,
        currentQueryState,
        getLibraryImages,
        libraryScrollRoot,
        openLightbox,
        openStandaloneLightbox,
        showToast,
        syncDateScrubberVisibility,
    });

    function currentLibraryView() {
        return mapController.getLibraryView();
    }

    function setLibraryView(mode) {
        return mapController.setLibraryView(mode);
    }

    function loadMap() {
        return mapController.loadMap();
    }

    function openLightboxById(id) {
        return mapController.openLightboxById(id);
    }

    return {
        currentLibraryView,
        loadMap,
        openLightboxById,
        setLibraryView,
    };
}
