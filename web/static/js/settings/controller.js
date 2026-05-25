import { recommendedMemoryGb as recommendedMemoryGbCore } from './display.js';
import {
    clearThumbnailCache as clearThumbnailCacheCore,
    installAIModel as installAIModelCore,
    pauseEmbeddings as pauseEmbeddingsCore,
    resetSettings as resetSettingsCore,
    resumeEmbeddings as resumeEmbeddingsCore,
    saveSettings as saveSettingsCore,
    startCachePregeneration as startCachePregenerationCore,
    stopCachePregeneration as stopCachePregenerationCore,
} from './actions.js';


export function createSettingsApi({
    collectSettingsForm,
    populateSettingsForm,
    rememberThumbnailOutput,
    renderSettingsMeta,
    setSettingsStatus,
    showToast,
    updateThumbnailChangeNotice,
    showConfirmModal,
    formatBytes,
    renderCacheSettingsStatus,
    renderAISettingsStatus,
    renderModelStatus,
    updateCacheProfileHint,
    getSettingsPageData = () => ({}),
    documentImpl = globalThis.document,
    recommendedMemoryGb = recommendedMemoryGbCore,
    saveSettings = saveSettingsCore,
    resetSettings = resetSettingsCore,
    clearThumbnailCache = clearThumbnailCacheCore,
    startCachePregeneration = startCachePregenerationCore,
    stopCachePregeneration = stopCachePregenerationCore,
    pauseEmbeddings = pauseEmbeddingsCore,
    resumeEmbeddings = resumeEmbeddingsCore,
    installAIModel = installAIModelCore,
} = {}) {
    const settingsActionOptions = () => ({
        collectSettingsForm,
        populateSettingsForm,
        rememberThumbnailOutput,
        renderSettingsMeta,
        setSettingsStatus,
        showToast,
        updateThumbnailChangeNotice,
    });
    const cacheActionOptions = () => ({
        renderCacheSettingsStatus,
        setSettingsStatus,
        showToast,
    });
    const embeddingActionOptions = () => ({
        renderAISettingsStatus,
        setSettingsStatus,
        showToast,
    });

    const api = {
        saveSettings: async () => {
            await saveSettings(settingsActionOptions());
        },
        resetSettings: () => resetSettings({
            populateSettingsForm,
            rememberThumbnailOutput,
            renderSettingsMeta,
            setSettingsStatus,
            showConfirmModal,
            showToast,
            updateThumbnailChangeNotice,
        }),
        clearThumbnailCache: () => clearThumbnailCache({
            formatBytes,
            renderSettingsMeta,
            setSettingsStatus,
            showConfirmModal,
            showToast,
        }),
        startCachePregeneration: async () => {
            await startCachePregeneration(cacheActionOptions());
        },
        stopCachePregeneration: async () => {
            await stopCachePregeneration(cacheActionOptions());
        },
        pauseEmbeddings: async () => {
            await pauseEmbeddings(embeddingActionOptions());
        },
        resumeEmbeddings: async () => {
            await resumeEmbeddings(embeddingActionOptions());
        },
        installAIModel: async (role = 'active') => {
            await installAIModel(role, {
                ...settingsActionOptions(),
                renderAISettingsStatus,
                renderModelStatus,
            });
        },
        applyRecommendedCache: () => {
            const profileInput = documentImpl?.getElementById?.('cache_profile');
            const memoryInput = documentImpl?.getElementById?.('memory_cache_gb');
            const pageData = getSettingsPageData() || {};
            if (profileInput) profileInput.value = 'original_heavy';
            if (memoryInput) memoryInput.value = recommendedMemoryGb(pageData.settings || {});
            updateCacheProfileHint();
            api.saveSettings();
        },
        pauseAllWork: () => {
            api.pauseEmbeddings();
            api.stopCachePregeneration();
        },
        resumeAllWork: () => {
            api.resumeEmbeddings();
            api.startCachePregeneration();
        },
    };

    return api;
}
