import { getCatalog } from './api.js';
import { on, setActiveLens, viewState } from './state.js';

const STORAGE_KEY = 'pa_d_quick_guide';
const SCHEMA = 1;
const GUIDE_ID = 'library-basics-v1';
const VALID_STATUSES = new Set(['available', 'pending', 'active', 'dismissed', 'complete']);
const ACTION_IDS = ['open-photo', 'pick-favorite', 'find-something', 'try-refine'];
const FILTER_KEYS = [
    'q', 'people', 'flag', 'folder', 'date_taken', 'file_type', 'camera', 'lens',
    'tag', 'orientation', 'compared', 'min_stars', 'import_batch', 'similarIds', 'collectionId',
];
const TASKS = [
    { id: 'open-photo', label: 'Open a photo', hint: 'Double-click, press Enter, or press E.' },
    { id: 'pick-favorite', label: 'Pick a favorite', hint: 'Press P on a photo you want to keep.' },
    { id: 'find-something', label: 'Find something', hint: 'Search by place, person, camera, or description.' },
    { id: 'try-refine', label: 'Try Refine', hint: 'Press R to make one quick choice.' },
];

let state = defaultState();
let hasStoredState = false;
let hasSeenImages = false;
let classificationToken = 0;

function defaultState() {
    return {
        schema: SCHEMA,
        guide: GUIDE_ID,
        eligible: false,
        status: 'available',
        completed: [],
    };
}

function normalizedState(value) {
    if (!value || value.schema !== SCHEMA || value.guide !== GUIDE_ID) return defaultState();
    const completed = Array.isArray(value.completed)
        ? [...new Set(value.completed.filter((id) => ACTION_IDS.includes(id)))]
        : [];
    return {
        schema: SCHEMA,
        guide: GUIDE_ID,
        eligible: Boolean(value.eligible),
        status: VALID_STATUSES.has(value.status) ? value.status : 'available',
        completed,
    };
}

function readState() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw == null) return defaultState();
        hasStoredState = true;
        return normalizedState(JSON.parse(raw));
    } catch {
        hasStoredState = true;
        return defaultState();
    }
}

function persistState() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
        // A blocked storage API must never prevent the library from opening.
    }
}

function updateState(patch) {
    state = normalizedState({ ...state, ...patch });
    persistState();
    renderGuide();
}

function guideVisible() {
    return state.eligible
        && state.status === 'active'
        && hasSeenImages
        && viewState.activeLens === 'grid';
}

function syncVisibility() {
    const root = document.getElementById('quick-guide');
    if (root) root.hidden = !guideVisible();
}

function renderGuide() {
    const root = document.getElementById('quick-guide');
    if (!root) return;
    const complete = ACTION_IDS.every((id) => state.completed.includes(id));
    const taskRows = TASKS.map((task) => {
        const done = state.completed.includes(task.id);
        return '<li class="quick-guide-task' + (done ? ' done' : '') + '">'
            + `<span class="quick-guide-check" aria-hidden="true">${done ? '✓' : ''}</span>`
            + `<span><b>${task.label}</b><small>${task.hint}</small></span></li>`;
    }).join('');
    root.innerHTML = '<div class="quick-guide-head">'
        + '<div><h2>Start here</h2>'
        + `<p aria-live="polite">${complete ? '4 of 4 complete' : `${state.completed.length} of 4 complete`}</p></div>`
        + '</div>'
        + `<ol class="quick-guide-tasks">${taskRows}</ol>`
        + (complete
            ? '<p class="quick-guide-ready">You’re ready. Your library gets better as you use it.</p>'
                + '<button class="mini-btn" id="quick-guide-done" type="button">Done</button>'
            : '<button class="quick-guide-dismiss" id="quick-guide-dismiss" type="button">Dismiss</button>');
    root.querySelector('#quick-guide-dismiss')?.addEventListener('click', () => {
        updateState({ status: 'dismissed' });
    });
    root.querySelector('#quick-guide-done')?.addEventListener('click', () => {
        updateState({ status: 'complete' });
    });
    syncVisibility();
}

function completeAction(actionId) {
    if (!state.eligible || state.status !== 'active' || state.completed.includes(actionId)) return;
    updateState({ completed: [...state.completed, actionId] });
}

function valueActive(value) {
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'boolean') return value;
    return String(value == null ? '' : value).trim().length > 0;
}

function scopeHasFilter(nextScope) {
    return Boolean(nextScope && FILTER_KEYS.some((key) => valueActive(nextScope[key])));
}

function activateWhenReady() {
    if (!state.eligible || !hasSeenImages || state.status !== 'pending') return;
    updateState({ status: 'active' });
}

function replayGuide() {
    classificationToken += 1;
    setActiveLens('grid');
    updateState({ eligible: true, status: 'active', completed: [] });
    document.getElementById('help-close')?.click();
}

async function classifyFirstRun(token) {
    const catalog = await getCatalog().catch(() => null);
    if (token !== classificationToken || hasStoredState) return;
    const sources = catalog && Array.isArray(catalog.sources) ? catalog.sources : null;
    if (sources == null) {
        updateState(defaultState());
        return;
    }
    updateState({
        eligible: sources.length === 0,
        status: sources.length === 0 ? 'pending' : 'available',
        completed: [],
    });
    activateWhenReady();
}

export function initQuickGuide() {
    state = readState();
    hasSeenImages = Array.isArray(viewState.images) && viewState.images.some(Boolean);
    renderGuide();

    on('images', (images) => {
        if (Array.isArray(images) && images.some(Boolean)) hasSeenImages = true;
        activateWhenReady();
        syncVisibility();
    });
    on('lens', (lens) => {
        if (lens === 'refine') completeAction('try-refine');
        syncVisibility();
    });
    on('loupe:open', () => completeAction('open-photo'));
    on('flags', (payload) => {
        if (payload && payload.flag === 'picked' && payload.committed === true) {
            completeAction('pick-favorite');
        }
    });
    on('scope', (nextScope) => {
        if (scopeHasFilter(nextScope)) completeAction('find-something');
    });
    on('refine:open', () => completeAction('try-refine'));

    // Delegate: shortcut_sheet.js replaces #help innerHTML at boot (before us).
    document.getElementById('help')?.addEventListener('click', (event) => {
        if (event.target.closest('#help-restart-guide')) replayGuide();
    });

    if (!hasStoredState) {
        const token = ++classificationToken;
        classifyFirstRun(token);
    }
}
