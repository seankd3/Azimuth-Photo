import { initState } from './state.js';
import { initToast } from './toast.js';
import { initSelection } from './selection.js';
import { initPanel } from './panel.js';
import { initRightPanel } from './panel_right.js';
import { initDrawer } from './drawer.js';
import { initContextbar } from './contextbar.js';
import { initOmnibox } from './omnibox.js';
import { initLenses } from './lenses.js';
import { initLoupe } from './loupe.js';
import { initRefine } from './refine.js';
import { initKeyboard } from './keyboard.js';
import { initFilters } from './filters.js';
import { initImporter } from './importer.js';
import { initSimilar } from './similar.js';
import { initGridContextMenu } from './context_menu.js';

async function boot() {
    initState();
    initToast();
    initSelection();
    initContextbar();
    initFilters();
    initImporter();
    initSimilar();
    initGridContextMenu();
    initOmnibox();
    initLoupe();
    initRefine();
    initRightPanel();
    initDrawer();
    initKeyboard();
    await initPanel();
    initLenses();
}

boot().catch((error) => {
    console.error('desktop boot failed', error);
});
