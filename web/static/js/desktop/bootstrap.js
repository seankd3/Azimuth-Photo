import { initState } from './state.js';
import { initKeywordsPanel } from './keywords_panel.js';
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
import { initShortcutSheet } from './shortcut_sheet.js';
import { initWatchedFolders } from './watched_folders.js';
import { initFilters } from './filters.js';
import { initImportStage } from './import_stage.js';
import { initSimilar } from './similar.js';
import { initGridContextMenu } from './context_menu.js';
import { initExportMenu } from './export_menu.js';
import { mountIconSprite } from '../icons.js';
import { initPanelSections } from './panel_sections.js';
import { initCullBrief } from './cull_brief.js';
import { initQuickGuide } from './quick_guide.js';
import { initLrRankingChip } from './lr_ranking_chip.js';

// One-time exorcism: earlier builds registered an offline service worker
// whose stale cache answered synthetic 504s over a healthy engine (see
// static/sw.js). Its own update fetch can fail, so installed clients
// cannot rely on the replacement stub — every boot purges any surviving
// registration and its caches directly. Harmless once clean.
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations()
        .then((regs) => Promise.all(regs.map((reg) => reg.unregister())))
        .catch(() => {});
    window.caches?.keys?.()
        .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
        .catch(() => {});
}

async function boot() {
    await mountIconSprite();
    initMotion();
    initState();
    initToast();
    initSelection();
    initContextbar();
    initFilters();
    initImportStage();
    initCullBrief();
    initKeywordsPanel();
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
    initWatchedFolders();
    initLrRankingChip();
    initShortcutSheet();
    initKeyboard();
    initQuickGuide();
    initLenses();
    await initPanel();
}

boot().catch((error) => {
    console.error('desktop boot failed', error);
});
