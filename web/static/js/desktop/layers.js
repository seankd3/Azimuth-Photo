/** Ordered foreground-layer inventory for shortcuts and lens interactions. */

const COMMON_FOREGROUND_LAYERS = [
    '.typed-confirm',
    '#collection-picker',
    '#collection-pop-menu:not([hidden])',
    '#grid-pop-menu:not([hidden])',
    '#export-pop-menu:not([hidden])',
    '#folder-pop-menu:not([hidden])',
    '#source-pop-menu:not([hidden])',
    '#deliver-overlay:not([hidden])',
    '#people-merge-pop',
];

export const FOREGROUND_LAYER_SELECTORS = {
    keyboard: COMMON_FOREGROUND_LAYERS,
    suggestions: [
        '.typed-confirm',
        '#scopebox.open',
        '#help:not([hidden])',
        '#filter-popover:not([hidden])',
        '#import-scrim:not([hidden])',
        ...COMMON_FOREGROUND_LAYERS.slice(1, -1),
        '#drawer-scrim:not([hidden])',
        '.person-card.menu-open',
        '#people-merge-pop',
    ],
};

export function foregroundLayerOpen(surface) {
    return (FOREGROUND_LAYER_SELECTORS[surface] || []).some((selector) => document.querySelector(selector));
}
