export const DEFAULT_UI_SETTINGS = Object.freeze({
    show_loupe_cache_status: true,
});


export function normalizeUiSettings(values = {}, defaults = DEFAULT_UI_SETTINGS) {
    return {
        ...defaults,
        show_loupe_cache_status: values.show_loupe_cache_status !== false,
    };
}


export function createUiSettingsLoader({
    fetchJsonImpl,
    onLoaded = () => {},
    defaultSettings = DEFAULT_UI_SETTINGS,
} = {}) {
    let uiSettings = { ...defaultSettings };
    let uiSettingsPromise = null;

    function getSettings() {
        return uiSettings;
    }

    function loadUiSettings() {
        if (uiSettingsPromise) return uiSettingsPromise;
        uiSettingsPromise = fetchJsonImpl('/api/ui/settings', { defaultValue: null })
            .then((data) => {
                const values = data?.settings || data || {};
                uiSettings = normalizeUiSettings(values, uiSettings);
                onLoaded(uiSettings);
                return uiSettings;
            })
            .catch(() => uiSettings)
            .finally(() => {
                uiSettingsPromise = null;
            });
        return uiSettingsPromise;
    }

    return {
        getSettings,
        loadUiSettings,
    };
}
