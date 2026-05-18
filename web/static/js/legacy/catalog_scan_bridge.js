import { createHomeScanController } from '../catalog/home_scan.js';
import { createScanEntrypoint } from '../catalog/scan_entrypoint.js';


export function createLegacyCatalogScanBridge({
    documentImpl = globalThis.document,
    fetchImpl = globalThis.fetch,
    setIntervalImpl = globalThis.setInterval,
    clearIntervalImpl = globalThis.clearInterval,
    setTimeoutImpl = globalThis.setTimeout,
    locationImpl = globalThis.window?.location,
    settingsScan,
    createHomeScanControllerImpl = createHomeScanController,
    createScanEntrypointImpl = createScanEntrypoint,
} = {}) {
    const homeScan = createHomeScanControllerImpl({
        documentImpl,
        fetchImpl,
        setIntervalImpl,
        clearIntervalImpl,
        setTimeoutImpl,
        locationImpl,
    });
    const scanEntrypoint = createScanEntrypointImpl({
        documentImpl,
        homeScan,
        settingsScan,
    });

    function startScan() {
        return scanEntrypoint.startScan();
    }

    return {
        startScan,
    };
}
