import { fetchJson } from '../api.js';
import { toggleAIPanel } from '../ai/status.js';

export function createLegacySharedRuntimeBridge({
    fetchJsonImpl = fetchJson,
    toggleAIPanelImpl = toggleAIPanel,
} = {}) {
    return {
        fetchJson: (...args) => fetchJsonImpl(...args),
        toggleAIPanel: (...args) => toggleAIPanelImpl(...args),
    };
}
