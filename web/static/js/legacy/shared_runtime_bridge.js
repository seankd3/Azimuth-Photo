import { fetchJson } from '../api.js';
import {
    toggleAIPanel,
    toggleBackgroundWorkPanel,
} from '../ai/status.js';

export function createLegacySharedRuntimeBridge({
    fetchJsonImpl = fetchJson,
    toggleAIPanelImpl = toggleAIPanel,
    toggleBackgroundWorkPanelImpl = toggleBackgroundWorkPanel,
} = {}) {
    return {
        fetchJson: (...args) => fetchJsonImpl(...args),
        toggleAIPanel: (...args) => toggleAIPanelImpl(...args),
        toggleBackgroundWorkPanel: (...args) => toggleBackgroundWorkPanelImpl(...args),
    };
}
