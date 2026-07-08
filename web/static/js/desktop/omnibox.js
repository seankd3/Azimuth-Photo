import { getPeople } from './api.js';
import {
    emit, on, scope, setScope, setSort, toggleBestOf,
} from './state.js';
import { scopeTokenHtml } from './contextbar.js';
import {
    exportCurrentScope, requestDeleteCurrentCollection, requestNewCollection,
    requestRenameCurrentCollection, requestShareCurrentCollection, toggleLeftPanel,
} from './panel.js';
import { switchLens } from './lenses.js';

const RECENT_KEY = 'pa_d_recent_scopes';
let people = null;
let rows = [];
let hot = -1;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const COMMANDS = [
    { glyph: '▸', label: 'Open Refine', kbd: 'R', run: () => emit('refine:open') },
    { glyph: '◇', label: 'Find duplicates', run: () => emit('duplicates:open') },
    { glyph: '⌯', label: 'Filter…', run: () => emit('filters:open') },
    { glyph: '＋', label: 'Import', run: () => emit('import:open') },
    { glyph: '★', label: 'Toggle Best-of', kbd: 'B', run: toggleBestOf },
    { glyph: '⇩', label: 'Export CSV', run: () => exportCurrentScope('csv') },
    { glyph: '⇩', label: 'Export JSON', run: () => exportCurrentScope('json') },
    { glyph: '⇩', label: 'Download files (zip)', run: () => exportCurrentScope('zip', 'original') },
    { glyph: '⊞', label: 'New collection', run: requestNewCollection },
    { glyph: '↗', label: 'Share this collection', when: () => Boolean(scope.collectionId), run: requestShareCurrentCollection },
    { glyph: '⊞', label: 'Rename this collection', when: () => Boolean(scope.collectionId), run: requestRenameCurrentCollection },
    { glyph: '⊞', label: 'Delete this collection', when: () => Boolean(scope.collectionId), run: requestDeleteCurrentCollection },
    { glyph: '▦', label: 'Switch lens: Grid', kbd: 'G', run: () => switchLens('grid') },
    { glyph: '☰', label: 'Switch lens: Events', kbd: 'E', run: () => switchLens('events') },
    { glyph: '◉', label: 'Switch lens: People', kbd: 'O', run: () => switchLens('people') },
    { glyph: '◈', label: 'Switch lens: Map', kbd: 'M', run: () => switchLens('map') },
    { glyph: '☰', label: 'Toggle left panel', kbd: '[', run: toggleLeftPanel },
    { glyph: '?', label: 'Keyboard shortcuts', kbd: '?', run: () => emit('help:open') },
    { glyph: '⌂', label: 'Clear scope / All Photos', run: () => setScope({}) },
    { glyph: '↓', label: 'Sort by Elo', run: () => setSort('elo') },
    { glyph: '◷', label: 'Sort by Date', run: () => setSort('date_taken') },
    { glyph: 'A', label: 'Sort by Filename', run: () => setSort('filename') },
];

function fuzzy(haystack, needle) {
    const hay = haystack.toLowerCase();
    const term = needle.toLowerCase().trim();
    if (!term) return true;
    let i = 0;
    for (const ch of hay) {
        if (ch === term[i]) i += 1;
        if (i === term.length) return true;
    }
    return false;
}

function recentScopes() {
    try {
        const values = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
        return Array.isArray(values) ? values : [];
    } catch {
        return [];
    }
}

function remember(scope) {
    const label = scope.collectionName || scope.personLabel || scope.q || scope.camera || scope.lens || 'All Photos';
    const values = recentScopes().filter((item) => JSON.stringify(item.scope) !== JSON.stringify(scope));
    values.unshift({ label, scope });
    localStorage.setItem(RECENT_KEY, JSON.stringify(values.slice(0, 6)));
}

async function loadPeople() {
    if (people) return people;
    const data = await getPeople(48);
    people = (data && (data.people || data.persons || data.results)) || [];
    return people;
}

function open() {
    document.getElementById('scopebox').classList.add('open');
}

function close() {
    document.getElementById('scopebox').classList.remove('open');
    hot = -1;
}

function rowHtml(row, index) {
    const face = row.thumb ? `<img class="sd-face" src="${esc(row.thumb)}" alt="">` : `<span class="sd-glyph">${row.glyph}</span>`;
    const meta = row.kbd ? `<kbd>${esc(row.kbd)}</kbd>` : esc(row.meta || '');
    return `<div class="sd-item ${index === hot ? 'hot' : ''}" role="option" data-index="${index}">${face}<span class="sd-label">${esc(row.label)}</span><span class="sd-meta">${meta}</span></div>`;
}

async function build() {
    const input = document.getElementById('scope-input');
    const term = input.value.trim();
    rows = [];
    if (input.value.startsWith('>')) {
        rows.push({ head: 'Commands' });
        const query = input.value.slice(1);
        for (const command of COMMANDS.filter((item) => (!item.when || item.when()) && fuzzy(item.label, query))) {
            rows.push(command);
        }
        if (rows.length === 1) rows.push({ empty: 'No matching command.' });
    } else if (term) {
        if (term.startsWith('camera:')) {
            rows.push({ glyph: '⌘', label: `Use ${term}`, run: () => apply({ camera: term.slice(7).trim() }) });
        } else if (term.startsWith('lens:')) {
            rows.push({ glyph: '⌘', label: `Use ${term}`, run: () => apply({ lens: term.slice(5).trim() }) });
        } else {
            rows.push({ glyph: '⌕', label: `Search for “${term}”`, run: () => apply({ q: term }) });
        }
        const allPeople = await loadPeople();
        const lower = term.toLowerCase();
        for (const person of allPeople) {
            const label = person.label || person.name || person.display_name || `Person ${person.id}`;
            if (!label.toLowerCase().includes(lower)) continue;
            rows.push({
                glyph: '◉',
                thumb: person.thumb_url,
                label,
                meta: person.image_count || person.face_count || '',
                run: () => apply({ people: person.id, personLabel: label, personThumb: person.thumb_url || '' }),
            });
            if (rows.length >= 8) break;
        }
    }
    const recents = recentScopes();
    if (recents.length) rows.push({ head: 'Recent scopes' });
    for (const item of recents) {
        rows.push({ glyph: '◷', label: item.label, run: () => apply(item.scope) });
    }
    render();
}

function render() {
    const drop = document.getElementById('scope-drop');
    let index = 0;
    const footer = '<div class="sd-foot">Use Filter for flags, people, folders, metadata, and rating.</div>';
    drop.innerHTML = rows.map((row) => {
        if (row.head) return `<div class="sd-head">${row.head}</div>`;
        if (row.empty) return `<div class="sd-item"><span class="sd-label">${esc(row.empty)}</span></div>`;
        return rowHtml(row, index++);
    }).join('') || '<div class="sd-item"><span class="sd-label">Type to search this archive</span></div>';
    drop.insertAdjacentHTML('beforeend', footer);
    hot = Math.min(hot, runnableRows().length - 1);
    for (const item of drop.querySelectorAll('.sd-item[data-index]')) {
        item.addEventListener('click', () => run(Number(item.dataset.index)));
    }
}

function runnableRows() {
    return rows.filter((row) => row.run);
}

function run(index) {
    const row = runnableRows()[index];
    if (!row) return;
    const input = document.getElementById('scope-input');
    const commandMode = input.value.startsWith('>');
    if (commandMode) {
        input.value = '';
        close();
        input.blur();
    }
    row.run();
}

function apply(patch) {
    remember(patch);
    setScope(patch);
    document.getElementById('scope-input').value = '';
    close();
}

function renderToken() {
    document.getElementById('scope-token-slot').innerHTML = scopeTokenHtml();
}

export function focusOmnibox(seed = null) {
    const input = document.getElementById('scope-input');
    if (seed != null) input.value = seed;
    input.focus();
    if (seed == null) input.select();
    else input.setSelectionRange(input.value.length, input.value.length);
    build();
    open();
}

export function openCommandPalette() {
    focusOmnibox('>');
}

export function initOmnibox() {
    const box = document.getElementById('scopebox');
    const input = document.getElementById('scope-input');
    input.addEventListener('focus', () => {
        build();
        open();
    });
    input.addEventListener('input', build);
    input.addEventListener('keydown', (event) => {
        const count = runnableRows().length;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            hot = Math.min(count - 1, hot + 1);
            render();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            hot = Math.max(0, hot - 1);
            render();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            run(hot >= 0 ? hot : 0);
        } else if (event.key === 'Escape') {
            input.value = '';
            input.blur();
            close();
        }
    });
    document.addEventListener('pointerdown', (event) => {
        if (!box.contains(event.target)) close();
    });
    on('scope', renderToken);
    on('meta', renderToken);
    renderToken();
    loadPeople();
}
