import {
    setScanBusy as setScanBusyCore,
    sourceState as sourceStateCore,
} from './status.js';
import {
    closeRemoveSourceDialog as closeRemoveSourceDialogCore,
    openRemoveSourceDialog as openRemoveSourceDialogCore,
    renderCatalogSources as renderCatalogSourcesCore,
} from './sources.js';
import {
    browseDirectory as browseDirectoryCore,
    browseDirectoryParent as browseDirectoryParentCore,
    chooseCatalogFolder as chooseCatalogFolderCore,
    selectBrowsedDirectory as selectBrowsedDirectoryCore,
    toggleDirectoryBrowser as toggleDirectoryBrowserCore,
    useBrowsedDirectory as useBrowsedDirectoryCore,
} from './browser.js';
import {
    addCatalogSource as addCatalogSourceCore,
    loadCatalogSources as loadCatalogSourcesCore,
    pollScanUntilDone as pollScanUntilDoneCore,
    removeCatalogSource as removeCatalogSourceCore,
    rescanCatalogSource as rescanCatalogSourceCore,
} from './actions.js';


export function createCatalogApi({
    getFallbackStats = () => ({}),
    setSettingsStatus,
    showToast,
    sourceState = sourceStateCore,
    setScanBusy = setScanBusyCore,
    renderCatalogSources = renderCatalogSourcesCore,
    loadCatalogSources = loadCatalogSourcesCore,
    pollScanUntilDone = pollScanUntilDoneCore,
    addCatalogSource = addCatalogSourceCore,
    rescanCatalogSource = rescanCatalogSourceCore,
    openRemoveSourceDialog = openRemoveSourceDialogCore,
    closeRemoveSourceDialog = closeRemoveSourceDialogCore,
    removeCatalogSource = removeCatalogSourceCore,
    chooseCatalogFolder = chooseCatalogFolderCore,
    browseDirectory = browseDirectoryCore,
    toggleDirectoryBrowser = toggleDirectoryBrowserCore,
    browseDirectoryParent = browseDirectoryParentCore,
    selectBrowsedDirectory = selectBrowsedDirectoryCore,
    useBrowsedDirectory = useBrowsedDirectoryCore,
} = {}) {
    let catalogSources = [];

    const api = {
        sourceState: (...args) => sourceState(...args),
        renderCatalogSources: (catalog) => {
            catalogSources = renderCatalogSources(catalog) || [];
            return catalogSources;
        },
        loadCatalogSources: () => loadCatalogSources({
            renderCatalogSources: api.renderCatalogSources,
            setSettingsStatus,
        }),
        setScanBusy: (busy, label = 'Add + Scan') => setScanBusy(busy, label),
        pollScanUntilDone: () => pollScanUntilDone({
            loadCatalogSources: api.loadCatalogSources,
            setScanBusy: api.setScanBusy,
            setSettingsStatus,
        }),
        addCatalogSource: () => addCatalogSource({
            fallbackStats: getFallbackStats(),
            pollScanUntilDone: api.pollScanUntilDone,
            renderCatalogSources: api.renderCatalogSources,
            setScanBusy: api.setScanBusy,
            setSettingsStatus,
            showToast,
        }),
        rescanCatalogSource: (sourceId) => rescanCatalogSource(sourceId, {
            pollScanUntilDone: api.pollScanUntilDone,
            setSettingsStatus,
            showToast,
        }),
        openRemoveSourceDialog: (sourceId) => {
            const source = catalogSources.find((item) => Number(item.id) === Number(sourceId));
            return openRemoveSourceDialog(source);
        },
        closeRemoveSourceDialog: () => closeRemoveSourceDialog(),
        removeCatalogSource: (sourceId, mode) => removeCatalogSource(sourceId, mode, {
            closeRemoveSourceDialog: api.closeRemoveSourceDialog,
            renderCatalogSources: api.renderCatalogSources,
            setSettingsStatus,
            showToast,
        }),
        chooseCatalogFolder: () => chooseCatalogFolder({ setSettingsStatus, showToast }),
        browseDirectory: (path = '') => browseDirectory(path, { showToast }),
        toggleDirectoryBrowser: () => toggleDirectoryBrowser({ browseDirectoryImpl: api.browseDirectory }),
        browseDirectoryParent: () => browseDirectoryParent({ browseDirectoryImpl: api.browseDirectory }),
        selectBrowsedDirectory: (...args) => selectBrowsedDirectory(...args),
        useBrowsedDirectory: (...args) => useBrowsedDirectory(...args),
    };

    return api;
}
