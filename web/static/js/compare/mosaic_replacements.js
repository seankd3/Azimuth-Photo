export const MOSAIC_REPLACEMENT_TARGET = 24;
export const MOSAIC_REPLACEMENT_FETCH_MIN = 12;
export const MOSAIC_REPLACEMENT_LOW_WATER = 8;
export const MOSAIC_REPLACEMENT_PROBE_CONCURRENCY = 4;
export const MOSAIC_REPLACEMENT_PRELOAD_TIMEOUT_MS = 120;


async function responseDataOrError(response, fallbackMessage) {
    let data = {};
    try {
        data = await response.json();
    } catch {}
    if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}


export function createMosaicReplacementBuffer({
    getMosaicFilling,
    setMosaicFilling,
    getMosaicImages,
    getMosaicReplacements,
    getMosaicRenderToken,
    getWarmupGeneration,
    enqueueWarmup,
    buildMosaicUrl,
    fetchImpl = globalThis.fetch,
    loadImageProbe,
    setCompareStats,
    updateCompareProgress,
    setTimeoutImpl = globalThis.setTimeout,
    replacementTarget = MOSAIC_REPLACEMENT_TARGET,
    replacementFetchMin = MOSAIC_REPLACEMENT_FETCH_MIN,
    replacementProbeConcurrency = MOSAIC_REPLACEMENT_PROBE_CONCURRENCY,
    replacementPreloadTimeoutMs = MOSAIC_REPLACEMENT_PRELOAD_TIMEOUT_MS,
} = {}) {
    function isCurrent(generation, renderToken) {
        return generation === getWarmupGeneration() && renderToken === getMosaicRenderToken();
    }

    function fillReplacements() {
        if (getMosaicFilling() || getMosaicReplacements().length >= replacementTarget) return false;
        setMosaicFilling(true);
        const generation = getWarmupGeneration();
        const renderToken = getMosaicRenderToken();
        enqueueWarmup(async () => {
            try {
                if (!isCurrent(generation, renderToken)) return;
                const needed = Math.max(
                    replacementFetchMin,
                    replacementTarget - getMosaicReplacements().length,
                );
                const excludeIds = [
                    ...getMosaicImages().map(img => img.id),
                    ...getMosaicReplacements().map(img => img.id),
                ].join(',');
                const res = await fetchImpl(buildMosaicUrl({ n: needed, exclude: excludeIds }));
                const data = await responseDataOrError(res, 'Mosaic replacements unavailable');
                if (!isCurrent(generation, renderToken)) return;
                if (data.stats) {
                    setCompareStats(data.stats);
                    updateCompareProgress();
                }

                const inBuffer = new Set(getMosaicReplacements().map(img => img.id));
                const candidates = [];
                for (const img of data.images || []) {
                    const onGrid = getMosaicImages().some((entry) => entry.id === img.id);
                    if (!onGrid && !inBuffer.has(img.id)) {
                        inBuffer.add(img.id);
                        candidates.push(img);
                    }
                }

                let readyCount = 0;
                const addWhenReady = async (img) => {
                    const probe = await loadImageProbe(img.thumb_url, {
                        priority: 'auto',
                        timeoutMs: replacementPreloadTimeoutMs,
                    });
                    if (!isCurrent(generation, renderToken)) return;
                    if (!probe.ok && !probe.timedOut) return;
                    const currentGrid = new Set(getMosaicImages().map((entry) => entry.id));
                    const replacements = getMosaicReplacements();
                    if (currentGrid.has(img.id) || replacements.some((entry) => entry.id === img.id)) return;
                    if (replacements.length >= replacementTarget) return;
                    replacements.push({
                        ...img,
                        cache_probe_deferred: Boolean(probe.timedOut),
                    });
                    readyCount++;
                };

                for (let start = 0; start < candidates.length; start += replacementProbeConcurrency) {
                    if (!isCurrent(generation, renderToken)) return;
                    if (getMosaicReplacements().length >= replacementTarget) break;
                    const chunk = candidates.slice(start, start + replacementProbeConcurrency);
                    await Promise.all(chunk.map(addWhenReady));
                }
                if (getMosaicReplacements().length < replacementFetchMin && candidates.length > readyCount) {
                    setTimeoutImpl(() => fillReplacements(), 300);
                }
            } catch {} finally {
                setMosaicFilling(false);
            }
        }, {
            generation,
            priority: 'high',
            onDrop: () => {
                setMosaicFilling(false);
            },
        });
        return true;
    }

    return {
        fillReplacements,
    };
}
