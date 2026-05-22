import { createCatalogApi } from '../catalog/controller.js';
import { renderCatalogSources as renderCatalogSourcesCore } from '../catalog/sources.js';
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
    cacheProfileLabel,
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

    function formatSetupCount(value) {
        return Math.max(0, Number(value || 0)).toLocaleString();
    }

    function setSetupStep(step, { badge, badgeClass, detail }) {
        const badgeEl = documentImpl.getElementById(`setup-step-${step}-badge`);
        const detailEl = documentImpl.getElementById(`setup-step-${step}-detail`);
        if (badgeEl) {
            badgeEl.className = `embedding-index-badge ${badgeClass || ''}`.trim();
            badgeEl.textContent = badge;
        }
        if (detailEl) {
            detailEl.textContent = detail;
        }
    }

    function renderSetupGuide(data = {}) {
        const guide = documentImpl.getElementById('setup-guide');
        if (!guide) return;

        const settings = data.settings || {};
        const catalog = data.catalog || {};
        const stats = catalog.stats || {};
        const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
        const activeSources = sources.filter((source) => source.included !== false);
        const onlineSources = activeSources.filter((source) => source.online !== false);
        const activeImages = Math.max(0, Number(stats.active_images ?? stats.total_images ?? 0) || 0);
        const catalogImages = Math.max(0, Number(stats.total_catalog_images ?? stats.total_images ?? activeImages) || 0);

        const summaryEl = documentImpl.getElementById('setup-guide-summary');
        if (summaryEl) {
            if (activeImages > 0) {
                summaryEl.textContent = `${formatSetupCount(activeImages)} active photos ready`;
            } else if (activeSources.length > 0) {
                summaryEl.textContent = 'Source added; scan will populate Library';
            } else {
                summaryEl.textContent = 'Add a source folder to begin';
            }
        }

        setSetupStep('catalog', activeSources.length > 0 ? {
            badge: 'Ready',
            badgeClass: 'ready',
            detail: `${formatSetupCount(activeSources.length)} source${activeSources.length === 1 ? '' : 's'} configured; ${formatSetupCount(onlineSources.length)} online.`,
        } : {
            badge: 'Next',
            badgeClass: 'missing',
            detail: 'Choose a mounted photo folder, then add it as a catalog source.',
        });

        setSetupStep('scan', activeImages > 0 ? {
            badge: 'Ready',
            badgeClass: 'ready',
            detail: `${formatSetupCount(activeImages)} active photos; ${formatSetupCount(catalogImages)} catalog rows tracked.`,
        } : activeSources.length > 0 ? {
            badge: 'Scan',
            badgeClass: 'scheduled',
            detail: 'Run Add + Scan or Rescan to fill the Library.',
        } : {
            badge: 'Waiting',
            badgeClass: 'missing',
            detail: 'Add a source before scanning.',
        });

        const workMode = settings.background_work_mode || selectedBackgroundWorkMode();
        setSetupStep('work', {
            badge: 'Ready',
            badgeClass: 'ready',
            detail: `${backgroundWorkModeLabel(workMode)} mode selected.`,
        });

        const memoryGb = Number(settings.memory_cache_gb ?? 0);
        const ssdGb = Number(settings.ssd_cache_gb ?? 0);
        setSetupStep('cache', {
            badge: 'Ready',
            badgeClass: 'ready',
            detail: `${memoryGb.toLocaleString()} GB RAM, ${ssdGb.toLocaleString()} GB SSD, ${cacheProfileLabel(settings.cache_profile)} priority.`,
        });

        const aiStatus = data.ai_status || {};
        const fastIndex = aiStatus.embedding_indexes?.fast || {};
        const modelStatus = data.model_status || {};
        const install = modelStatus.install || {};
        const fastInstalled = Boolean(fastIndex.installed || aiStatus.model_installed || modelStatus.installed);
        const fastInstalling = Boolean(fastIndex.installing || aiStatus.installing || install.running);
        const fastTotal = Math.max(0, Number(fastIndex.total_images ?? activeImages) || 0);
        const fastEmbedded = Math.max(0, Number(fastIndex.embedded ?? aiStatus.embedded ?? 0) || 0);
        const fastRemaining = Math.max(0, Number(fastIndex.remaining ?? Math.max(fastTotal - fastEmbedded, 0)) || 0);
        if (fastInstalling) {
            setSetupStep('ai', {
                badge: 'Installing',
                badgeClass: 'scheduled',
                detail: install.message || 'Model install is running.',
            });
        } else if (fastInstalled && activeImages > 0 && fastRemaining > 0) {
            setSetupStep('ai', {
                badge: 'Indexing',
                badgeClass: 'scheduled',
                detail: `${formatSetupCount(fastRemaining)} of ${formatSetupCount(fastTotal)} images left for Daily Search.`,
            });
        } else if (fastInstalled) {
            setSetupStep('ai', {
                badge: 'Ready',
                badgeClass: 'ready',
                detail: activeImages > 0 ? 'Daily Search is installed and ready.' : 'Model is installed; indexing starts after scan.',
            });
        } else {
            setSetupStep('ai', {
                badge: 'Optional',
                badgeClass: 'scheduled',
                detail: 'Install the 2B model when you want semantic search and similarity.',
            });
        }

        const peopleStatus = data.people_status || {};
        const peopleCounts = peopleStatus.counts || {};
        const peopleEnabled = settings.people_scan_enabled !== false && peopleStatus.active !== false;
        const detectedFaces = Math.max(0, Number(peopleCounts.detected_faces || 0) || 0);
        const peopleCount = Math.max(0, Number(peopleCounts.people || 0) || 0);
        if (!peopleEnabled) {
            setSetupStep('people', {
                badge: 'Optional',
                badgeClass: 'scheduled',
                detail: 'People scanning is disabled.',
            });
        } else if (detectedFaces > 0) {
            setSetupStep('people', {
                badge: 'Ready',
                badgeClass: 'ready',
                detail: `${formatSetupCount(peopleCount)} people, ${formatSetupCount(detectedFaces)} detected faces.`,
            });
        } else if (activeImages > 0) {
            setSetupStep('people', {
                badge: 'Enabled',
                badgeClass: 'scheduled',
                detail: 'Enabled; scans cached previews as they become available.',
            });
        } else {
            setSetupStep('people', {
                badge: 'Optional',
                badgeClass: 'scheduled',
                detail: 'Enabled by default; useful after catalog scan and preview cache.',
            });
        }
    }

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

    function renderCatalogSourcesForSettings(catalog) {
        const sources = renderCatalogSourcesCore(catalog);
        settingsPageData = {
            ...(settingsPageData || {}),
            catalog: catalog || {},
        };
        renderSetupGuide(settingsPageData);
        return sources;
    }

    const catalogApi = createCatalogApi({
        getFallbackStats: () => settingsPageData?.catalog?.stats || {},
        renderCatalogSources: renderCatalogSourcesForSettings,
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
        renderSetupGuide(data);
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
