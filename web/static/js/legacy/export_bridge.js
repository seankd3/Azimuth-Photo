import { exportRankings as exportRankingsCore } from '../export/actions.js';

export function createLegacyExportBridge({
    exportRankingsImpl = exportRankingsCore,
    getQueryState,
    getSort,
} = {}) {
    return {
        exportRankings: (format) => exportRankingsImpl(format, {
            queryState: getQueryState?.() || {},
            sort: getSort?.(),
        }),
    };
}
