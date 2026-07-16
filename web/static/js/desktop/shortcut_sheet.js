export const SHORTCUTS = [
    { area: 'Library', key: '/', label: 'Focus search' },
    { area: 'Library', key: 'Ctrl/Cmd K', label: 'Open commands' },
    { area: 'Library', key: '?', label: 'Show this sheet' },
    { area: 'Library', key: 'Esc', label: 'Close the active overlay' },
    { area: 'Library', key: 'G', label: 'Grid' },
    { area: 'Library', key: 'E', label: 'Open focused photo in Loupe' },
    { area: 'Library', key: 'D', label: 'Open focused photo in Develop' },
    { area: 'Library', key: 'O / M / Y / H', label: 'People / Map / Events / Publishing' },
    { area: 'Library', key: 'Arrows', label: 'Move focus' },
    { area: 'Library', key: 'Home / End', label: 'First / last loaded photo' },
    { area: 'Library', key: 'Page Up / Down', label: 'Move by a page' },
    { area: 'Library', key: 'Enter', label: 'Open focused photo in Loupe' },
    { area: 'Library', key: 'Space', label: 'Toggle focused selection' },
    { area: 'Library', key: 'Ctrl/Cmd A', label: 'Select loaded photos' },
    { area: 'Library', key: 'P / X / U', label: 'Pick / reject / clear flag' },
    { area: 'Library', key: '1–5', label: 'Set or clear the Elo floor' },
    { area: 'Library', key: 'B', label: 'Toggle Best Of' },
    { area: 'Library', key: 'F', label: 'Open filters' },
    { area: 'Library', key: 'S', label: 'Stack selection or expand focused stack' },
    { area: 'Library', key: 'J', label: 'Cycle grid density' },
    { area: 'Library', key: 'Delete', label: 'Trash selection' },
    { area: 'Library', key: '[ / ]', label: 'Toggle left / info panel' },
    { area: 'Loupe', key: 'Left / Right', label: 'Previous / next photo' },
    { area: 'Loupe', key: 'Shift Arrows', label: 'Pan while zoomed' },
    { area: 'Loupe', key: 'Home / End', label: 'First / last photo' },
    { area: 'Loupe', key: '+ / − / 0', label: 'Zoom / fit' },
    { area: 'Loupe', key: 'Z / Space', label: 'Toggle fit and 100%' },
    { area: 'Loupe', key: 'L / I / V', label: 'Lights / info / rendition' },
    { area: 'Loupe', key: 'P / X / U', label: 'Pick / reject / clear flag' },
    { area: 'Loupe', key: 'G / Esc', label: 'Close Loupe' },
    { area: 'Loupe', key: 'Delete', label: 'Trash current photo' },
    { area: 'Develop', key: 'Left / Right', label: 'Previous / next photo' },
    { area: 'Develop', key: 'Z', label: 'Toggle zoom' },
    { area: 'Develop', key: 'Space', label: 'Hold to zoom' },
    { area: 'Develop', key: '\\', label: 'Hold before view' },
    { area: 'Develop', key: 'R', label: 'Open / close Crop & Straighten' },
    { area: 'Develop', key: 'O', label: 'Cycle crop overlay while cropping' },
    { area: 'Develop', key: 'K', label: 'Open Masking tools' },
    { area: 'Develop', key: 'W', label: 'Toggle white-balance picker' },
    { area: 'Develop', key: 'J', label: 'Toggle clipping overlays' },
    { area: 'Develop', key: 'Y / Alt Y', label: 'Before/after vertical / horizontal' },
    { area: 'Develop', key: 'Ctrl/Cmd Z', label: 'Undo edit' },
    { area: 'Develop', key: 'Ctrl/Cmd Shift Z', label: 'Redo edit' },
    { area: 'Develop', key: 'Ctrl/Cmd Shift C / V', label: 'Copy / paste settings' },
    { area: 'Develop', key: "Ctrl/Cmd '", label: 'Create virtual copy' },
    { area: 'Culling', key: 'R', label: 'Open Refine' },
    { area: 'Culling', key: '1–9 / 0', label: 'Pick Mosaic candidate' },
    { area: 'Culling', key: 'Left / Right', label: 'Move Mosaic focus / choose Duel side' },
    { area: 'Culling', key: 'Enter', label: 'Pick focused Mosaic candidate' },
    { area: 'Culling', key: 'Ctrl/Cmd Z', label: 'Undo Refine pick' },
    { area: 'Culling', key: 'K / Enter', label: 'Keep stack cover and trash the rest' },
    { area: 'Culling', key: 'C', label: 'Make focused stack photo the cover' },
    { area: 'Culling', key: 'U', label: 'Unstack focused stack' },
    { area: 'Painter', key: 'K', label: 'Paint the armed keyword on focused photo' },
    { area: 'Painter', key: 'Esc', label: 'Exit keyword painter' },
];

let root = null;

// Keep the shortcut catalog independently loadable for the binding contract check.
function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));
}

function keycapMarkup(shortcut) {
    return shortcut.split(' / ').map((option) => `<span class="shortcut-sheet-keys">${option.split(' ').map((part) => `<kbd>${escapeHtml(part)}</kbd>`).join('<span class="shortcut-sheet-plus">+</span>')}</span>`).join('<span class="shortcut-sheet-or">or</span>');
}

function groupMarkup(area) {
    const entries = SHORTCUTS.filter((shortcut) => shortcut.area === area);
    return `<section class="shortcut-sheet-group" aria-labelledby="shortcut-sheet-${area.toLowerCase()}">
        <h3 id="shortcut-sheet-${area.toLowerCase()}">${area}</h3>
        <dl>${entries.map((shortcut) => `<div><dt>${keycapMarkup(shortcut.key)}</dt><dd>${escapeHtml(shortcut.label)}</dd></div>`).join('')}</dl>
    </section>`;
}

export function initShortcutSheet() {
    root = document.getElementById('help');
    if (!root) return;
    root.innerHTML = `<div id="help-card" class="shortcut-sheet-card" role="document">
        <button id="help-close" class="icon-btn" data-tip="Close (Esc)" aria-label="Close">×</button>
        <p class="shortcut-sheet-eyebrow">Keyboard-first photo flow</p>
        <h2>Shortcuts</h2>
        <p class="shortcut-sheet-intro">The keys you need while sorting, selecting, and editing.</p>
        <div class="shortcut-sheet-grid">${['Library', 'Loupe', 'Develop', 'Culling', 'Painter'].map(groupMarkup).join('')}</div>
    </div>`;
}

export function shortcutSheetOpen() {
    return Boolean(root && !root.hidden);
}
