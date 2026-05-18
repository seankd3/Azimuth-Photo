import { createSettingsPageController } from '../settings/page.js';


export function createLegacySettingsPageBridge({
    showToast,
    showConfirmModal,
    formatBytes,
    initVisibilityRefresh,
    initBottomBarMeasurement,
    createSettingsPageControllerImpl = createSettingsPageController,
} = {}) {
    const settingsPage = createSettingsPageControllerImpl({
        showToast,
        showConfirmModal,
        formatBytes,
        initVisibilityRefresh,
        initBottomBarMeasurement,
    });

    return {
        addCatalogSource: (...args) => settingsPage.addCatalogSource(...args),
        applyRecommendedCache: (...args) => settingsPage.applyRecommendedCache(...args),
        browseDirectory: (...args) => settingsPage.browseDirectory(...args),
        browseDirectoryParent: (...args) => settingsPage.browseDirectoryParent(...args),
        chooseCatalogFolder: (...args) => settingsPage.chooseCatalogFolder(...args),
        clearThumbnailCache: (...args) => settingsPage.clearThumbnailCache(...args),
        closeRemoveSourceDialog: (...args) => settingsPage.closeRemoveSourceDialog(...args),
        initSettings: (...args) => settingsPage.initSettings(...args),
        installAIModel: (...args) => settingsPage.installAIModel(...args),
        openRemoveSourceDialog: (...args) => settingsPage.openRemoveSourceDialog(...args),
        pauseAllWork: (...args) => settingsPage.pauseAllWork(...args),
        pauseEmbeddings: (...args) => settingsPage.pauseEmbeddings(...args),
        refreshSettingsMetaIfActive: (...args) => settingsPage.refreshSettingsMetaIfActive(...args),
        removeCatalogSource: (...args) => settingsPage.removeCatalogSource(...args),
        rescanCatalogSource: (...args) => settingsPage.rescanCatalogSource(...args),
        resetSettings: (...args) => settingsPage.resetSettings(...args),
        resumeAllWork: (...args) => settingsPage.resumeAllWork(...args),
        resumeEmbeddings: (...args) => settingsPage.resumeEmbeddings(...args),
        saveSettings: (...args) => settingsPage.saveSettings(...args),
        selectBrowsedDirectory: (...args) => settingsPage.selectBrowsedDirectory(...args),
        startCachePregeneration: (...args) => settingsPage.startCachePregeneration(...args),
        startScan: (...args) => settingsPage.startScan(...args),
        stopCachePregeneration: (...args) => settingsPage.stopCachePregeneration(...args),
        toggleDirectoryBrowser: (...args) => settingsPage.toggleDirectoryBrowser(...args),
        useBrowsedDirectory: (...args) => settingsPage.useBrowsedDirectory(...args),
    };
}
