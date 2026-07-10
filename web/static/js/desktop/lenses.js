import { eventGap, initEvents, mountEvents, setEventGap, unmountEvents } from './events.js';
import { initDateScrubber } from './date_scrubber.js';
import { initGrid, mountGrid, unmountGrid } from './grid.js';
import { initMap, mountMap, unmountMap } from './map.js';
import { initTimeline, mountTimeline, unmountTimeline } from './timeline.js';
import { initPeople, mountPeople, unmountPeople } from './people.js';
import { mountRefine, unmountRefine } from './refine.js';
import { mountSuggestions, unmountSuggestions } from './suggestions.js';
import { mountLoupe, unmountLoupe } from './loupe.js';
import { mountDuplicates, unmountDuplicates } from './duplicates.js';
import { mountTrash, unmountTrash } from './trash.js';
import { mountShared, unmountShared } from './shared.js';
import { on, setActiveLens, viewState } from './state.js';

const LENSES = {
    grid: { mount: mountGrid, unmount: unmountGrid },
    events: { mount: mountEvents, unmount: unmountEvents },
    timeline: { mount: mountTimeline, unmount: unmountTimeline },
    people: { mount: mountPeople, unmount: unmountPeople },
    map: { mount: mountMap, unmount: unmountMap },
    refine: { mount: mountRefine, unmount: unmountRefine },
    suggestions: { mount: mountSuggestions, unmount: unmountSuggestions },
    loupe: { mount: mountLoupe, unmount: unmountLoupe },
    duplicates: { mount: mountDuplicates, unmount: unmountDuplicates },
    trash: { mount: mountTrash, unmount: unmountTrash },
    shared: { mount: mountShared, unmount: unmountShared },
};

let current = null;

function syncChrome(lens) {
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        const active = button.dataset.view === lens;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
    document.getElementById('shell').classList.toggle('right-hidden', lens === 'events' || lens === 'timeline' || lens === 'map' || lens === 'suggestions' || lens === 'shared');
    document.getElementById('event-gap-wrap').hidden = lens !== 'events';
    const sortDisabled = lens === 'people' || lens === 'timeline' || lens === 'map' || lens === 'refine' || lens === 'suggestions' || lens === 'loupe' || lens === 'duplicates' || lens === 'trash' || lens === 'shared';
    document.getElementById('sort-select').disabled = sortDisabled;
    document.getElementById('sort-dir').disabled = sortDisabled;
    document.getElementById('thumb-size').disabled = lens === 'people' || lens === 'map' || lens === 'refine' || lens === 'suggestions' || lens === 'loupe' || lens === 'trash' || lens === 'shared';
    document.getElementById('btn-refine').classList.toggle('active', lens === 'refine');
    document.getElementById('find-duplicates')?.classList.toggle('active', lens === 'duplicates');
    document.querySelector('[data-lib="trash"]')?.classList.toggle('active', lens === 'trash');
    document.getElementById('shared-view')?.classList.toggle('active', lens === 'shared');
    document.getElementById('ctx-mid').hidden = lens === 'shared';
    document.getElementById('ctx-right').hidden = lens === 'shared';
}

function activate(lens) {
    const next = LENSES[lens] ? lens : 'grid';
    if (current === next) {
        syncChrome(next);
        return;
    }
    if (current) LENSES[current].unmount();
    current = next;
    syncChrome(next);
    LENSES[next].mount();
}

export function switchLens(lens) {
    setActiveLens(lens);
}

export function activeLens() {
    return current || viewState.activeLens || 'grid';
}

export function initLenses() {
    initGrid();
    initEvents();
    initTimeline();
    initPeople();
    initMap();
    initDateScrubber();
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        button.addEventListener('click', () => switchLens(button.dataset.view));
    }
    const gap = document.getElementById('event-gap');
    gap.value = String(eventGap());
    gap.addEventListener('change', () => setEventGap(gap.value));
    on('lens', activate);
    activate(viewState.activeLens || 'grid');
}
