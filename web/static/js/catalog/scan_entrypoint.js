export function createScanEntrypoint({
    documentImpl = document,
    homeScan = null,
    settingsScan = null,
} = {}) {
    function startScan() {
        if (documentImpl.getElementById('folder-input')) {
            return homeScan?.startScan?.();
        }
        return settingsScan?.();
    }

    return { startScan };
}
