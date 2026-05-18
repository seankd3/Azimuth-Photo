import {
    createImagePreloader,
    createWarmupManager,
    loadImageProbe,
    withTimeout,
} from '../warmup.js';
import { createNeighborWarmupController } from '../warmup_neighbors.js';

export function createLegacyWarmupBridge({
    createImagePreloaderImpl = createImagePreloader,
    createWarmupManagerImpl = createWarmupManager,
    loadImageProbeImpl = loadImageProbe,
    withTimeoutImpl = withTimeout,
    createNeighborWarmupControllerImpl = createNeighborWarmupController,
} = {}) {
    return {
        createImagePreloader: (...args) => createImagePreloaderImpl(...args),
        createNeighborWarmupController: (...args) => createNeighborWarmupControllerImpl(...args),
        createWarmupManager: (...args) => createWarmupManagerImpl(...args),
        loadImageProbe: (...args) => loadImageProbeImpl(...args),
        withTimeout: (...args) => withTimeoutImpl(...args),
    };
}
