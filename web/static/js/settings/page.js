import { createCatalogApi } from '../catalog/controller.js';
import { renderCatalogSources as renderCatalogSourcesCore } from '../catalog/sources.js';
import { renderCacheTierGuide } from '../cache/guide.js';
import { setSettingsStatus } from './status.js';
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
    cacheProfileLabel,
    THUMB_OUTPUT_FIELDS,
} from './display.js';
import {
    collectSettingsForm as collectSettingsFormCore,
    populateSettingsForm as populateSettingsFormCore,
    rememberThumbnailOutput,
    updateCacheProfileHint,
    updateThumbnailChangeNotice,
} from './form.js';
import { createSettingsApi } from './controller.js';


const SETTINGS_FIELDS = [
    'embed_model_preset',
    'thumb_size_sm',
    'thumb_size_md',
    'thumb_size_lg',
    'thumb_quality',
    'memory_cache_gb',
    'cache_profile',
    'ssd_cache_dir',
    'ssd_cache_gb',
    'search_similarity_threshold',
    'show_loupe_cache_status',
    'face_model_id',
    'face_model_dir',
    'face_detection_size',
    'face_similarity_threshold',
    'face_merge_suggestion_threshold',
];
const SETTINGS_META_POLL_MS = 30000;
const SETTINGS_STATUS_TIMEOUT_MS = 5000;


function staleSettingsStatus(kind, latencyMs = SETTINGS_STATUS_TIMEOUT_MS) {
    if (kind === 'cache') {
        return {
            pregen: {},
            disk: { tiers: {} },
            memory: { tiers: {} },
            counts_stale: true,
            status_stale: true,
            latency_ms: latencyMs,
            active: false,
        };
    }
    if (kind === 'people') {
        return {
            active: false,
            worker: { state: 'stale' },
            counts: { pending_cached_images: 0, scan: {} },
            counts_stale: true,
            status_stale: true,
            latency_ms: latencyMs,
        };
    }
    if (kind === 'catalog') {
        return {
            sources: [],
            stats: {},
            counts_stale: true,
            status_stale: true,
            latency_ms: latencyMs,
        };
    }
    return {
        embedding_index: {},
        worker_state: 'stale',
        counts_stale: true,
        status_stale: true,
        embedding_manual_pause: true,
        latency_ms: latencyMs,
    };
}


async function fetchSettingsStatusJson(url, {
    fetchImpl,
    kind,
    timeoutMs = SETTINGS_STATUS_TIMEOUT_MS,
}) {
    const started = Date.now();
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let timer = null;
    try {
        const timeout = new Promise((_, reject) => {
            timer = globalThis.setTimeout(() => {
                controller?.abort?.();
                reject(new Error('status timeout'));
            }, Math.max(50, Number(timeoutMs) || SETTINGS_STATUS_TIMEOUT_MS));
        });
        const request = fetchImpl(url, controller ? { signal: controller.signal } : undefined)
            .then((res) => {
                if (!res?.ok) throw new Error('status request failed');
                return res.json();
            });
        return await Promise.race([request, timeout]);
    } catch {
        return staleSettingsStatus(kind, Math.max(0, Date.now() - started));
    } finally {
        if (timer) globalThis.clearTimeout(timer);
    }
}


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export function createSettingsPageController({
    showToast,
    showConfirmModal,
    formatBytes,
    initVisibilityRefresh,
    initBottomBarMeasurement,
    startAIStatusPolling = () => {},
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

        const memoryGb = Number(settings.memory_cache_gb ?? 0);
        const ssdGb = Number(settings.ssd_cache_gb ?? 0);
        setSetupStep('cache', {
            badge: 'Ready',
            badgeClass: 'ready',
            detail: `${memoryGb.toLocaleString()} GB RAM, ${ssdGb.toLocaleString()} GB SSD, ${cacheProfileLabel(settings.cache_profile)} priority.`,
        });

        const aiStatus = data.ai_status || {};
        const activeIndex = aiStatus.embedding_index || {};
        const modelStatus = data.model_status || {};
        const install = modelStatus.install || {};
        const activeInstalled = Boolean(activeIndex.installed || aiStatus.model_installed || modelStatus.installed);
        const activeInstalling = Boolean(activeIndex.installing || aiStatus.installing || install.running);
        const activeTotal = Math.max(0, Number(activeIndex.total_images ?? activeImages) || 0);
        const activeEmbedded = Math.max(0, Number(activeIndex.embedded ?? aiStatus.embedded ?? 0) || 0);
        const activeRemaining = Math.max(0, Number(activeIndex.remaining ?? Math.max(activeTotal - activeEmbedded, 0)) || 0);
        if (activeInstalling) {
            setSetupStep('ai', {
                badge: 'Installing',
                badgeClass: 'scheduled',
                detail: install.message || 'Model install is running.',
            });
        } else if (activeInstalled && activeImages > 0 && activeRemaining > 0) {
            setSetupStep('ai', {
                badge: 'Indexing',
                badgeClass: 'scheduled',
                detail: `${formatSetupCount(activeRemaining)} of ${formatSetupCount(activeTotal)} images left for AI embeddings/search.`,
            });
        } else if (activeInstalled) {
            setSetupStep('ai', {
                badge: 'Ready',
                badgeClass: 'ready',
                detail: activeImages > 0 ? 'AI embeddings/search is installed and ready.' : 'Model is installed; indexing starts after scan.',
            });
        } else {
            setSetupStep('ai', {
                badge: 'Optional',
                badgeClass: 'scheduled',
                detail: 'Install the selected model when you want semantic search and similarity.',
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
        renderWorkBanner(data.ai_status, data.cache_stats, data.people_status);
        renderCacheTierGuide(data.cache_stats, data.settings);
        if (data.catalog) renderCatalogSources(data.catalog);
        renderSetupGuide(data);
        updateCacheProfileHint();
    }

    function renderWorkBanner(aiStatus, cacheStatus, peopleStatus = null) {
        const el = documentImpl.getElementById('work-banner');
        if (!el) return;
        el.innerHTML = workBannerHtml(aiStatus, cacheStatus, peopleStatus);
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

    function bindSettingsActions() {
        if (documentImpl.body?.dataset?.paSettingsActionsBound === '1') return;
        if (documentImpl.body?.dataset) {
            documentImpl.body.dataset.paSettingsActionsBound = '1';
        }
        documentImpl.addEventListener('click', (event) => {
            const control = event.target?.closest?.('[data-action]');
            if (!control) return;
            const action = control.dataset.action;
            const sourceId = Number(control.dataset.sourceId || 0);
            const path = control.dataset.path || '';
            const handled = true;
            switch (action) {
                case 'choose-catalog-folder':
                    event.preventDefault();
                    chooseCatalogFolder();
                    break;
                case 'toggle-directory-browser':
                    event.preventDefault();
                    toggleDirectoryBrowser();
                    break;
                case 'add-catalog-source':
                    event.preventDefault();
                    addCatalogSource();
                    break;
                case 'browse-directory-parent':
                    event.preventDefault();
                    browseDirectoryParent();
                    break;
                case 'use-browsed-directory':
                    event.preventDefault();
                    useBrowsedDirectory();
                    break;
                case 'browse-directory':
                    event.preventDefault();
                    browseDirectory(path || '/');
                    break;
                case 'select-browsed-directory':
                    event.preventDefault();
                    selectBrowsedDirectory(path);
                    break;
                case 'rescan-catalog-source':
                    event.preventDefault();
                    rescanCatalogSource(sourceId);
                    break;
                case 'open-remove-source-dialog':
                    event.preventDefault();
                    openRemoveSourceDialog(sourceId);
                    break;
                case 'remove-catalog-source':
                    event.preventDefault();
                    removeCatalogSource(sourceId, control.dataset.policy || 'keep');
                    break;
                case 'close-remove-source-dialog':
                    event.preventDefault();
                    closeRemoveSourceDialog();
                    break;
                case 'install-ai-model':
                    event.preventDefault();
                    installAIModel();
                    break;
                case 'pause-embeddings':
                    event.preventDefault();
                    pauseEmbeddings();
                    break;
                case 'resume-embeddings':
                    event.preventDefault();
                    resumeEmbeddings();
                    break;
                case 'apply-recommended-cache':
                    event.preventDefault();
                    applyRecommendedCache();
                    break;
                case 'pause-all-work':
                    event.preventDefault();
                    pauseAllWork();
                    break;
                case 'resume-all-work':
                    event.preventDefault();
                    resumeAllWork();
                    break;
                case 'save-settings':
                    event.preventDefault();
                    saveSettings();
                    break;
                case 'reset-settings':
                    event.preventDefault();
                    resetSettings();
                    break;
                case 'clear-thumbnail-cache':
                    event.preventDefault();
                    clearThumbnailCache();
                    break;
                default:
                    if (!handled) event.preventDefault();
            }
        });
    }

    async function loadSettingsPage(showStatus = true) {
        const res = await fetchImpl('/api/settings');
        const data = await responseDataOrError(res, 'Settings unavailable');
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
        const [cacheStats, aiStatus, peopleStatus, catalogStatus] = await Promise.all([
            fetchSettingsStatusJson('/api/cache/status', { fetchImpl, kind: 'cache' }),
            fetchSettingsStatusJson('/api/ai/status', { fetchImpl, kind: 'ai' }),
            fetchSettingsStatusJson('/api/people/status', { fetchImpl, kind: 'people' }),
            fetchSettingsStatusJson('/api/catalog', { fetchImpl, kind: 'catalog' }),
        ]);
        const catalog = catalogStatus?.counts_stale && settingsPageData?.catalog
            ? settingsPageData.catalog
            : catalogStatus;
        const data = {
            ...settingsPageData,
            cache_stats: cacheStats,
            ai_status: aiStatus,
            people_status: peopleStatus,
            catalog,
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
        startAIStatusPolling(750, { immediate: true });
        bindSettingsActions();
        const form = documentImpl.getElementById('settings-form');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                saveSettings();
            });
        }
        documentImpl.getElementById('cache_profile')?.addEventListener('change', updateCacheProfileHint);
        documentImpl.getElementById('embed_model_preset')?.addEventListener('change', applySelectedEmbeddingPreset);
        for (const field of THUMB_OUTPUT_FIELDS) {
            documentImpl.getElementById(field)?.addEventListener('input', updateThumbnailChangeNotice);
        }

        try {
            await loadSettingsPage(false);
            const folderInput = documentImpl.getElementById('scan-folder');
            try {
                const folderRes = await fetchImpl('/api/scan/folder');
                const folderData = await responseDataOrError(folderRes, 'Scan folder unavailable');
                if (folderInput && folderData.folder) folderInput.value = folderData.folder;
            } catch {}
            setSettingsStatus('Ready. Use Background Work for per-job pause/resume controls; Save applies Catalog settings.', 'muted');
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
