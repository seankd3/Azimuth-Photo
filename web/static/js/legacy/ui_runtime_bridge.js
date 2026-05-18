import { createAIStatusPoller } from '../ai/poller.js';
import {
    initBottomBarMeasurement as initBottomBarMeasurementUi,
    initVisibilityRefresh as initVisibilityRefreshUi,
    updateBottomBarHeightVar as updateBottomBarHeightVarUi,
} from '../ui.js';
import { createUiSettingsLoader } from '../settings/ui_settings.js';

export function createLegacyUiRuntimeBridge({
    documentImpl,
    windowImpl = globalThis.window,
    fetchJsonImpl,
    onBottomBarMeasured = () => {},
    refreshSettingsMetaIfActive = null,
    onUiSettingsLoaded = () => {},
} = {}) {
    const aiStatusPoller = createAIStatusPoller({
        documentImpl,
        initVisibilityRefresh,
    });
    const uiSettingsLoader = createUiSettingsLoader({
        fetchJsonImpl,
        onLoaded: onUiSettingsLoaded,
    });

    function updateBottomBarHeightVar() {
        updateBottomBarHeightVarUi({
            documentImpl,
            onMeasured: onBottomBarMeasured,
        });
    }

    function initBottomBarMeasurement() {
        initBottomBarMeasurementUi({
            documentImpl,
            windowImpl,
            onMeasured: onBottomBarMeasured,
        });
    }

    function startAIStatusPolling(initialDelayMs = 0, { immediate = false } = {}) {
        aiStatusPoller.start(initialDelayMs);
        if (immediate && !documentImpl?.hidden) {
            aiStatusPoller.poll();
        }
    }

    function initVisibilityRefresh() {
        initVisibilityRefreshUi({
            documentImpl,
            onVisible: () => {
                aiStatusPoller.handleVisible();
                refreshSettingsMetaIfActive?.()?.catch?.(() => {});
            },
        });
    }

    function loadUiSettings() {
        return uiSettingsLoader.loadUiSettings();
    }

    function getUiSettings() {
        return uiSettingsLoader.getSettings();
    }

    return {
        getUiSettings,
        initBottomBarMeasurement,
        initVisibilityRefresh,
        loadUiSettings,
        startAIStatusPolling,
        updateBottomBarHeightVar,
    };
}
