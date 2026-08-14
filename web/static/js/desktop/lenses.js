import { initCollections, mountCollections, unmountCollections } from './collections.js';
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

import { mountSystemLens, unmountSystemLens } from './system_lens.js';
import { on, setActiveLens, viewState } from './state.js';

const LENSES = {
    grid: { mount: mountGrid, unmount: unmountGrid },
    collections: { mount: mountCollections, unmount: unmountCollections },
    timeline: { mount: mountTimeline, unmount: unmountTimeline },
    people: { mount: mountPeople, unmount: unmountPeople },
    map: { mount: mountMap, unmount: unmountMap },
    refine: { mount: mountRefine, unmount: unmountRefine },
    suggestions: { mount: mountSuggestions, unmount: unmountSuggestions },
    loupe: { mount: mountLoupe, unmount: unmountLoupe },
    duplicates: { mount: mountDuplicates, unmount: unmountDuplicates },
    trash: { mount: mountTrash, unmount: unmountTrash },

    system: { mount: mountSystemLens, unmount: unmountSystemLens },
};

let current = null;

function syncChrome(lens) {
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        const active = button.dataset.view === lens;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
    document.getElementById('shell').classList.toggle('right-hidden', lens === 'collections' || lens === 'timeline' || lens === 'map' || lens === 'suggestions' || lens === 'system');
    const sortDisabled = lens === 'collections' || lens === 'people' || lens === 'timeline' || lens === 'map' || lens === 'refine' || lens === 'suggestions' || lens === 'loupe' || lens === 'duplicates' || lens === 'trash' || lens === 'system';
    document.getElementById('sort-select').disabled = sortDisabled;
    document.getElementById('sort-dir').disabled = sortDisabled;
    document.getElementById('thumb-size').disabled = lens === 'collections' || lens === 'people' || lens === 'map' || lens === 'refine' || lens === 'suggestions' || lens === 'loupe' || lens === 'trash' || lens === 'system';
    document.getElementById('btn-refine').classList.toggle('active', lens === 'refine');
    document.getElementById('find-duplicates')?.classList.toggle('active', lens === 'duplicates');
    document.querySelector('[data-lib="trash"]')?.classList.toggle('active', lens === 'trash');
    document.getElementById('ctx-mid').hidden = lens === 'collections';
    document.getElementById('ctx-right').hidden = lens === 'collections';
}

function activate(lens) {
    const next = LENSES[lens] ? lens : 'grid';
    if (current === next) {
        syncChrome(next);
        return;
    }
    if (current) {
        LENSES[current].unmount();
    } else {
        // The HTML ships Grid active for first paint. If a tool lens wins the
        // startup race before Grid mounts, clear that static class so two
        // lenses can never remain visible together.
        for (const view of document.querySelectorAll('.view.active')) view.classList.remove('active');
    }
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
    initCollections();
    initTimeline();
    initPeople();
    initMap();
    initDateScrubber();
    for (const button of document.querySelectorAll('#view-switch button[data-view]')) {
        button.addEventListener('click', async () => {
            if (button.dataset.view === 'develop') {
                const { openDevelop } = await import('./develop/develop.js');
                openDevelop();
                return;
            }
            switchLens(button.dataset.view);
        });
    }
    on('lens', activate);
    activate(viewState.activeLens || 'grid');
}
