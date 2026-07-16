// White-balance eyedropper: solve Temperature/Tint so a sampled patch renders
// neutral through OUR white-balance model (the same wbMatrix/wbGains the GL
// renderer applies). Coarse-to-fine search in mired space — deterministic,
// ~1k evaluations, sub-frame cost.
import { wbGains, wbMatrix } from './gl.js';

const TEMP_MIN = 2000;
const TEMP_MAX = 50000;
const TINT_MIN = -150;
const TINT_MAX = 150;

function neutralError(sample, color, asShotT, asShotTint, temperature, tint) {
    const matrix = color ? wbMatrix(color, asShotT, asShotTint, temperature, tint) : null;
    let r;
    let g;
    let b;
    if (matrix) {
        r = matrix[0][0] * sample[0] + matrix[0][1] * sample[1] + matrix[0][2] * sample[2];
        g = matrix[1][0] * sample[0] + matrix[1][1] * sample[1] + matrix[1][2] * sample[2];
        b = matrix[2][0] * sample[0] + matrix[2][1] * sample[1] + matrix[2][2] * sample[2];
    } else {
        const gains = wbGains(asShotT, asShotTint, temperature, tint);
        r = sample[0] * gains[0];
        g = sample[1] * gains[1];
        b = sample[2] * gains[2];
    }
    const reference = Math.max(g, 1e-6);
    return ((r - g) / reference) ** 2 + ((b - g) / reference) ** 2;
}

export function solveWhiteBalance(sample, color, asShotT = 5500, asShotTint = 0) {
    if (!Array.isArray(sample) || sample.length < 3 || Math.max(...sample) <= 0) return null;
    let bestTemperature = asShotT;
    let bestTint = asShotTint || 0;
    let bestError = Infinity;
    let miredLow = 1e6 / TEMP_MAX;
    let miredHigh = 1e6 / TEMP_MIN;
    let tintLow = TINT_MIN;
    let tintHigh = TINT_MAX;
    for (let round = 0; round < 4; round += 1) {
        const steps = round === 0 ? 24 : 9;
        for (let i = 0; i <= steps; i += 1) {
            const mired = miredLow + (miredHigh - miredLow) * (i / steps);
            const temperature = Math.max(TEMP_MIN, Math.min(TEMP_MAX, 1e6 / mired));
            for (let j = 0; j <= steps; j += 1) {
                const tint = tintLow + (tintHigh - tintLow) * (j / steps);
                const error = neutralError(sample, color, asShotT, asShotTint, temperature, tint);
                if (error < bestError) {
                    bestError = error;
                    bestTemperature = temperature;
                    bestTint = tint;
                }
            }
        }
        const miredSpan = (miredHigh - miredLow) / 4;
        const tintSpan = (tintHigh - tintLow) / 4;
        const bestMired = 1e6 / bestTemperature;
        miredLow = Math.max(1e6 / TEMP_MAX, bestMired - miredSpan / 2);
        miredHigh = Math.min(1e6 / TEMP_MIN, bestMired + miredSpan / 2);
        tintLow = Math.max(TINT_MIN, bestTint - tintSpan / 2);
        tintHigh = Math.min(TINT_MAX, bestTint + tintSpan / 2);
    }
    return {
        temperature: Math.round(bestTemperature / 10) * 10,
        tint: Math.round(bestTint),
        residual: bestError,
    };
}

export function sampleBasePatch(base, u, v, radius = 2) {
    if (!base?.rgba || !base.width || !base.height) return null;
    const x = Math.round(u * (base.width - 1));
    const y = Math.round(v * (base.height - 1));
    const sums = [0, 0, 0];
    let count = 0;
    for (let dy = -radius; dy <= radius; dy += 1) {
        const row = Math.min(base.height - 1, Math.max(0, y + dy));
        for (let dx = -radius; dx <= radius; dx += 1) {
            const col = Math.min(base.width - 1, Math.max(0, x + dx));
            const at = (row * base.width + col) * 4;
            sums[0] += base.rgba[at];
            sums[1] += base.rgba[at + 1];
            sums[2] += base.rgba[at + 2];
            count += 1;
        }
    }
    return count ? [sums[0] / count, sums[1] / count, sums[2] / count] : null;
}
