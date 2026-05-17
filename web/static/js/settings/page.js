import { createCatalogApi } from '../catalog/controller.js';
import { renderCacheTierGuide } from '../cache/guide.js';
import {
    backgroundWorkStatusText,
    setSettingsStatus,
} from './status.js';
import {
    renderAutoTuningStatus,
    renderCacheSettingsStatus,
} from './cache_status.js';
import {
    applySelectedEmbeddingPreset,
    renderAISettingsStatus,
    renderEmbeddingModelPresets,
    renderModelStatus,
} from './ai_status.js';
import { renderPeopleSettingsStatus } from './people_status.js';
import { workBannerHtml } from './work_banner.js';
import {
    backgroundWorkModeLabel,
    THUMB_OUTPUT_FIELDS,
} from './display.js';
import {
    collectSettingsForm as collectSettingsFormCore,
    populateSettingsForm as populateSettingsFormCore,
    rememberThumbnailOutput,
    renderDeepSearchSchedule,
    renderDeepSearchTerms,
    selectedBackgroundWorkMode,
    setBackgroundWorkMode,
    updateCacheProfileHint,
    updateThumbnailChangeNotice,
} from './form.js';
import { createSettingsApi } from './controller.js';


const SETTINGS_FIELDS = [
    'embed_model_preset',
    'embed_model_id',
    'embed_model_revision',
    'embed_model_dir',
    'embed_model_dim',
    'thumb_size_sm',
    'thumb_size_md',
    'thumb_size_lg',
    'thumb_quality',
    'memory_cache_gb',
    'background_work_mode',
    'cache_profile',
    'ssd_cache_dir',
    'ssd_cache_gb',
    'pregenerate_on_idle',
    'embed_batch_size',
    'defer_ai_on_startup',
    'deep_search_terms',
    'deep_search_schedule_enabled',
    'deep_search_schedule_days',
    'deep_search_schedule_start',
    'deep_search_schedule_end',
    'deep_search_schedule_timezone',
    'search_similarity_threshold',
    'show_loupe_cache_status',
    'face_model_id',
    'face_model_dir',
    'face_detection_size',
    'face_similarity_threshold',
    'face_merge_suggestion_threshold',
];
const SETTINGS_META_POLL_MS = 30000;


export function createSettingsPageController({
    showToast,
    showConfirmModal,
    formatBytes,
    initVisibilityRefresh,
    initBottomBarMeasurement,
    fetchImpl = globalThis.fetch,
    documentImpl = globalThis.document,
    setIntervalImpl = globalThis.setInterval,
    clearIntervalImpl = globalThis.clearInterval,
} = {}) {
    let settingsPoller = null;
    let settingsPageData = null;

    function renderBackgroundWorkStatus(cacheStats, aiStatus) {
        const statusEl = documentImpl.getElementById('background-work-governor-status');
        const heavyEl = documentImpl.getElementById('background-work-heavy-status');
        const workMode = selectedBackgroundWorkMode();
        const modeLabel = backgroundWorkModeLabel(workMode);
        const status = backgroundWorkStatusText(cacheStats, aiStatus, modeLabel);
        if (statusEl) {
            statusEl.textContent = status.governorText;
        }
        if (heavyEl) {
            heavyEl.textContent = status.heavyText;
        }
    }

    const catalogApi = createCatalogApi({
        getFallbackStats: () => settingsPageData?.catalog?.stats || {},
        setSettingsStatus,
        showToast,
    });
    const {
        renderCatalogSources,
        addCatalogSource,
        rescanCatalogSource,
        openRemoveSourceDialog,
        closeRemoveSourceDialog,
        removeCatalogSource,
        chooseCatalogFolder,
        browseDirectory,
        toggleDirectoryBrowser,
        browseDirectoryParent,
        selectBrowsedDirectory,
        useBrowsedDirectory,
    } = catalogApi;

    function renderSettingsMeta(data) {
        settingsPageData = data || settingsPageData;
        const pathEl = documentImpl.getElementById('settings-path');
        renderEmbeddingModelPresets(data.embedding_model_presets || []);
        renderCacheSettingsStatus(data.cache_stats);
        if (pathEl && data.settings_path) {
            pathEl.textContent = data.settings_path;
        }
        renderAutoTuningStatus(data.settings);
        renderModelStatus(data.model_status);
        renderAISettingsStatus(data.ai_status);
        renderPeopleSettingsStatus(data.people_status);
        renderBackgroundWorkStatus(data.cache_stats, data.ai_status);
        renderWorkBanner(data.ai_status, data.cache_stats);
        renderCacheTierGuide(data.cache_stats, data.settings);
        if (data.catalog) renderCatalogSources(data.catalog);
        updateCacheProfileHint();
    }

    function renderWorkBanner(aiStatus, cacheStatus) {
        const el = documentImpl.getElementById('work-banner');
        if (!el) return;
        el.innerHTML = workBannerHtml(aiStatus, cacheStatus);
    }

    function populateSettingsForm(settings) {
        populateSettingsFormCore(settings, { fields: SETTINGS_FIELDS, settingsPageData });
    }

    function collectSettingsForm() {
        return collectSettingsFormCore({ fields: SETTINGS_FIELDS, settingsPageData });
    }

    const settingsApi = createSettingsApi({
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
        getSettingsPageData: () => settingsPageData,
    });
    const {
        saveSettings,
        applyRecommendedCache,
        resetSettings,
        clearThumbnailCache,
        startCachePregeneration,
        stopCachePregeneration,
        installAIModel,
        pauseEmbeddings,
        resumeEmbeddings,
        pauseAllWork,
        resumeAllWork,
    } = settingsApi;

    async function loadSettingsPage(showStatus = true) {
        const res = await fetchImpl('/api/settings');
        const data = await res.json();
        settingsPageData = data;
        renderEmbeddingModelPresets(data.embedding_model_presets || []);
        populateSettingsForm(data.settings || {});
        rememberThumbnailOutput(data.settings || {});
        updateThumbnailChangeNotice();
        renderSettingsMeta(data);
        if (showStatus) {
            setSettingsStatus('Loaded current settings.', 'muted');
        }
        return data;
    }

    async function refreshSettingsMeta() {
        if (documentImpl.hidden) return settingsPageData || {};
        if (!settingsPageData) return loadSettingsPage(false);
        const [cacheRes, aiRes, peopleRes] = await Promise.all([
            fetchImpl('/api/cache/status'),
            fetchImpl('/api/ai/status'),
            fetchImpl('/api/people/status'),
        ]);
        const [cacheStats, aiStatus, peopleStatus] = await Promise.all([
            cacheRes.json(),
            aiRes.json(),
            peopleRes.json(),
        ]);
        const data = {
            ...settingsPageData,
            cache_stats: cacheStats,
            ai_status: aiStatus,
            people_status: peopleStatus,
        };
        renderSettingsMeta(data);
        return data;
    }

    function refreshSettingsMetaIfActive() {
        if (!settingsPoller) return Promise.resolve(settingsPageData || {});
        return refreshSettingsMeta();
    }

    async function initSettings() {
        initVisibilityRefresh();
        initBottomBarMeasurement();
        const form = documentImpl.getElementById('settings-form');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                saveSettings();
            });
        }
        documentImpl.getElementById('cache_profile')?.addEventListener('change', updateCacheProfileHint);
        for (const input of documentImpl.querySelectorAll('input[name="background_work_mode_choice"]')) {
            input.addEventListener('change', () => setBackgroundWorkMode(input.value));
        }
        documentImpl.getElementById('embed_model_preset')?.addEventListener('change', applySelectedEmbeddingPreset);
        documentImpl.getElementById('deep_search_terms')?.addEventListener('input', (event) => {
            renderDeepSearchTerms(event.target.value);
        });
        documentImpl.getElementById('deep_search_schedule_enabled')?.addEventListener('change', () => renderDeepSearchSchedule());
        documentImpl.getElementById('deep_search_schedule_start')?.addEventListener('input', () => renderDeepSearchSchedule());
        documentImpl.getElementById('deep_search_schedule_end')?.addEventListener('input', () => renderDeepSearchSchedule());
        documentImpl.getElementById('deep_search_schedule_timezone')?.addEventListener('change', () => renderDeepSearchSchedule());
        for (const input of documentImpl.querySelectorAll('[data-deep-search-day]')) {
            input.addEventListener('change', () => renderDeepSearchSchedule());
        }
        for (const field of THUMB_OUTPUT_FIELDS) {
            documentImpl.getElementById(field)?.addEventListener('input', updateThumbnailChangeNotice);
        }

        try {
            const settingsData = await loadSettingsPage(false);
            renderCatalogSources(settingsData.catalog || {});
            const stats = settingsData.catalog?.stats || {};
            const folderInput = documentImpl.getElementById('scan-folder');
            const totalEl = documentImpl.getElementById('scan-total-images');
            if (totalEl) totalEl.textContent = Number(stats.active_images ?? stats.total_images ?? 0).toLocaleString();
            try {
                const folderRes = await fetchImpl('/api/scan/folder');
                const folderData = await folderRes.json();
                if (folderInput && folderData.folder) folderInput.value = folderData.folder;
            } catch {}

            setSettingsStatus('Ready. Save to apply changes immediately.', 'muted');
            if (settingsPoller) clearIntervalImpl(settingsPoller);
            settingsPoller = setIntervalImpl(() => refreshSettingsMeta().catch(() => {}), SETTINGS_META_POLL_MS);
        } catch (err) {
            setSettingsStatus(`Could not load settings: ${err.message}`, 'error');
        }
    }

    return {
        initSettings,
        refreshSettingsMeta,
        refreshSettingsMetaIfActive,
        saveSettings,
        applyRecommendedCache,
        resetSettings,
        clearThumbnailCache,
        startCachePregeneration,
        stopCachePregeneration,
        installAIModel,
        startScan: addCatalogSource,
        addCatalogSource,
        rescanCatalogSource,
        openRemoveSourceDialog,
        closeRemoveSourceDialog,
        removeCatalogSource,
        chooseCatalogFolder,
        toggleDirectoryBrowser,
        browseDirectory,
        browseDirectoryParent,
        selectBrowsedDirectory,
        useBrowsedDirectory,
        pauseEmbeddings,
        resumeEmbeddings,
        pauseAllWork,
        resumeAllWork,
    };
}
