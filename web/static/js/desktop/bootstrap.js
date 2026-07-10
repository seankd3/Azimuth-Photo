import { initState } from './state.js';
import { initMotion } from './motion.js';
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
import { initDuplicates } from './duplicates.js';
import { initTrash } from './trash.js';
import { initKeyboard } from './keyboard.js';
import { initFilters } from './filters.js';
import { initImporter } from './importer.js';
import { initSimilar } from './similar.js';
import { initGridContextMenu } from './context_menu.js';
import { initExportMenu } from './export_menu.js';
import { mountIconSprite } from '../icons.js';
import { initPanelSections } from './panel_sections.js';
import { initCullBrief } from './cull_brief.js';

async function boot() {
    await mountIconSprite();
    initMotion();
    initState();
    initToast();
    initSelection();
    initContextbar();
    initFilters();
    initImporter();
    initCullBrief();
    initSimilar();
    initGridContextMenu();
    initExportMenu();
    initOmnibox();
    initLoupe();
    initRefine();
    initDuplicates();
    initTrash();
    initRightPanel();
    initPanelSections();
    initDrawer();
    initKeyboard();
    await initPanel();
    initLenses();
}

boot().catch((error) => {
    console.error('desktop boot failed', error);
});
