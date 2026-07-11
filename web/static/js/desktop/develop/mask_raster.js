/** Quarter-resolution mask rasterizer; math twin of features/develop/masks.py. */
import {
    LOCAL_BRUSH_DEFAULT_RADIUS, LOCAL_BRUSH_GAUSSIAN_SIGMA,
    LOCAL_COLOR_SIGMA_MIN, LOCAL_COLOR_SIGMA_RANGE, LOCAL_EXPOSURE_EV_SCALE,
    LOCAL_MASK_DOWNSAMPLE, LOCAL_RANGE_EPSILON, LOCAL_RENDER_CAP,
    LOCAL_SLIDER_SCALE, LOCAL_WB_MIRED_SCALE, LUMA_BLUE, LUMA_GREEN, LUMA_RED,
    OKLAB_M1, OKLAB_M2,
} from './ops_constants.js';

const number = (values, key, fallback = 0) => {
    const result = Number(values?.[key]);
    return Number.isFinite(result) ? result : fallback;
};
const truth = (value, fallback = false) => value == null ? fallback : value === true || value === 1 || String(value).toLowerCase() === 'true';
const clamp = (value, low = 0, high = 1) => Math.min(Math.max(value, low), high);
const smoothstep = (edge0, edge1, value) => {
    if (Math.abs(edge1 - edge0) <= LOCAL_RANGE_EPSILON) return value >= edge1 ? 1 : 0;
    const t = clamp((value - edge0) / (edge1 - edge0));
    return t * t * (3 - 2 * t);
};

/** Sole Adobe-native local normalization seam; exact twin of Python. */
export function localToSlider(local, key) {
    const value = clamp(number(local, key), -1, 1);
    if (key === 'LocalExposure2012') return value * LOCAL_EXPOSURE_EV_SCALE;
    if (key === 'LocalTemperature') return value * LOCAL_WB_MIRED_SCALE;
    return value * LOCAL_SLIDER_SCALE;
}

function rasterSize(width, height, downsample = LOCAL_MASK_DOWNSAMPLE) {
    return [Math.max(1, Math.ceil(width / downsample)), Math.max(1, Math.ceil(height / downsample))];
}

function normalizedGrid(width, height, { regionWidth = width, regionHeight = height, canvasWidth = regionWidth, canvasHeight = regionHeight, offsetX = 0, offsetY = 0 } = {}) {
    const x = Float32Array.from({ length: width }, (_, i) => (offsetX + (i + .5) * regionWidth / width) / Math.max(1, canvasWidth));
    const y = Float32Array.from({ length: height }, (_, i) => (offsetY + (i + .5) * regionHeight / height) / Math.max(1, canvasHeight));
    return { x, y, canvasWidth, canvasHeight };
}

export function rasterizeGradient(mask, width, height, grid = normalizedGrid(width, height)) {
    const result = new Float32Array(width * height);
    const zx = number(mask, 'ZeroX'), zy = number(mask, 'ZeroY');
    const dx = number(mask, 'FullX', 1) - zx, dy = number(mask, 'FullY') - zy;
    const length2 = dx * dx + dy * dy;
    if (length2 <= LOCAL_RANGE_EPSILON) return result;
    for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
        result[y * width + x] = smoothstep(0, 1, ((grid.x[x] - zx) * dx + (grid.y[y] - zy) * dy) / length2);
    }
    return result;
}

export function rasterizeRadial(mask, width, height, grid = normalizedGrid(width, height)) {
    const result = new Float32Array(width * height);
    const left = number(mask, 'Left'), right = number(mask, 'Right', 1), top = number(mask, 'Top'), bottom = number(mask, 'Bottom', 1);
    const cx = (left + right) * .5, cy = (top + bottom) * .5;
    const rx = Math.max(Math.abs(right - left) * .5, LOCAL_RANGE_EPSILON);
    const ry = Math.max(Math.abs(bottom - top) * .5, LOCAL_RANGE_EPSILON);
    const angle = number(mask, 'Angle') * Math.PI / 180, cosine = Math.cos(angle), sine = Math.sin(angle);
    const feather = clamp(number(mask, 'Feather', .5));
    for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
        const dx = grid.x[x] - cx, dy = grid.y[y] - cy;
        const px = cosine * dx + sine * dy, py = -sine * dx + cosine * dy;
        const rho = Math.hypot(px / rx, py / ry);
        let value = feather <= LOCAL_RANGE_EPSILON ? Number(rho <= 1) : 1 - smoothstep(1, 1 + feather, rho);
        if (truth(mask.Flipped)) value = 1 - value;
        result[y * width + x] = value;
    }
    return result;
}

function brushDabs(raw) {
    if (!Array.isArray(raw)) return [];
    const result = [];
    let currentRadius = LOCAL_BRUSH_DEFAULT_RADIUS;
    for (const entry of raw) {
        const parts = String(entry).trim().split(/\s+/);
        if (parts[0]?.toLowerCase() === 'd' && parts.length >= 3) result.push({ x: Number(parts[1]), y: Number(parts[2]), radius: null });
        else if (parts[0]?.toLowerCase() === 'r' && parts.length >= 2 && Number.isFinite(Number(parts[1]))) {
            currentRadius = Math.max(Number(parts[1]), LOCAL_RANGE_EPSILON);
            if (result.length && result.at(-1).radius == null) result.at(-1).radius = currentRadius;
        }
    }
    for (const dab of result) if (dab.radius == null) dab.radius = currentRadius;
    return result.filter((dab) => Number.isFinite(dab.x) && Number.isFinite(dab.y));
}

export function rasterizeBrush(mask, width, height, grid = normalizedGrid(width, height)) {
    const result = new Float32Array(width * height);
    let flow = number(mask, 'Flow', 1); if (flow > 1) flow /= 100; flow = clamp(flow);
    let hardness = number(mask, 'CenterWeight'); if (hardness > 1) hardness /= 100; hardness = clamp(hardness);
    const longest = Math.max(grid.canvasWidth, grid.canvasHeight, 1);
    // Catalog row 50607 proves x is /width and y is /height; radius is the
    // long-edge fraction used by Lightroom's circular brush size.
    for (const dab of brushDabs(mask.Dabs)) {
        const radius = Math.max(dab.radius * longest, LOCAL_RANGE_EPSILON);
        const hardRadius = radius * hardness, softWidth = Math.max(radius - hardRadius, LOCAL_RANGE_EPSILON);
        for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
            const distance = Math.hypot((grid.x[x] - dab.x) * grid.canvasWidth, (grid.y[y] - dab.y) * grid.canvasHeight);
            if (distance > radius) continue;
            const t = Math.max(distance - hardRadius, 0) / softWidth;
            const stamp = Math.exp(-.5 * (t / LOCAL_BRUSH_GAUSSIAN_SIGMA) ** 2);
            const i = y * width + x;
            result[i] = Math.min(result[i] + stamp * flow, 1);
        }
    }
    return result;
}

function quad(raw) {
    const values = Array.isArray(raw) ? raw.slice(0, 4).map(Number) : (String(raw || '').match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?/gi) || []).slice(0, 4).map(Number);
    if (values.length !== 4 || values.some((value) => !Number.isFinite(value))) return [0, 0, 1, 1];
    if (Math.max(...values.map(Math.abs)) > 1) for (let i = 0; i < 4; i += 1) values[i] /= 255;
    return values.map((value) => clamp(value));
}

function srgbToLinear(value) {
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
}

function linearToOklab(rgb) {
    const lms = OKLAB_M1.map((row) => Math.cbrt(row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2]));
    return OKLAB_M2.map((row) => row[0] * lms[0] + row[1] * lms[1] + row[2] * lms[2]);
}

function sampledColors(rangeMask) {
    let raw = ['SampledColors', 'ColorSamples', 'Colors', 'PointModels'].map((key) => rangeMask?.[key]).find((value) => value != null) || [];
    if (!Array.isArray(raw)) raw = raw?.PointModel || [raw];
    const result = [];
    for (let item of raw) {
        if (item && typeof item === 'object') item = item.Color || item.RGB || item.Value || item;
        const values = (String(item).match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?/gi) || []).slice(0, 3).map(Number);
        if (values.length !== 3) continue;
        if (Math.max(...values.map(Math.abs)) > 1) for (let i = 0; i < 3; i += 1) values[i] /= 255;
        result.push(values.map((value) => clamp(value)));
    }
    return result;
}

function normalizeImage(image, width, height) {
    if (!image) return new Float32Array(width * height * 3);
    if (image.width === width && image.height === height && image.data) {
        const channels = image.data.length / (width * height);
        const scale = image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray ? 1 / 255 : 1;
        const result = new Float32Array(width * height * 3);
        for (let i = 0; i < width * height; i += 1) for (let c = 0; c < 3; c += 1) result[i * 3 + c] = image.data[i * channels + c] * scale;
        return result;
    }
    return image.data || image;
}

export function rasterizeLuminanceRange(rangeMask, image, width, height) {
    const pixels = normalizeImage(image, width, height), result = new Float32Array(width * height);
    const [lowSoft, low, high, highSoft] = quad(rangeMask?.LumRange);
    for (let i = 0; i < result.length; i += 1) {
        const luma = pixels[i * 3] * LUMA_RED + pixels[i * 3 + 1] * LUMA_GREEN + pixels[i * 3 + 2] * LUMA_BLUE;
        result[i] = smoothstep(lowSoft, low, luma) * (1 - smoothstep(high, highSoft, luma));
    }
    return result;
}

export function rasterizeColorRange(rangeMask, image, width, height) {
    const colors = sampledColors(rangeMask);
    const result = new Float32Array(width * height);
    if (!colors.length) return result;
    const samples = colors.map((rgb) => linearToOklab(rgb.map(srgbToLinear)));
    const pixels = normalizeImage(image, width, height);
    let amount = number(rangeMask, 'ColorAmount', .5); if (amount > 1) amount /= 100;
    const sigma = LOCAL_COLOR_SIGMA_MIN + clamp(amount) * LOCAL_COLOR_SIGMA_RANGE;
    for (let i = 0; i < result.length; i += 1) {
        const lab = linearToOklab([srgbToLinear(pixels[i * 3]), srgbToLinear(pixels[i * 3 + 1]), srgbToLinear(pixels[i * 3 + 2])]);
        let distance2 = Infinity;
        for (const sample of samples) distance2 = Math.min(distance2, (lab[1] - sample[1]) ** 2 + (lab[2] - sample[2]) ** 2);
        const colorWeight = Math.exp(-.5 * distance2 / Math.max(sigma * sigma, LOCAL_RANGE_EPSILON));
        const [lowSoft, low, high, highSoft] = quad(rangeMask?.LumRange);
        const luma = pixels[i * 3] * LUMA_RED + pixels[i * 3 + 1] * LUMA_GREEN + pixels[i * 3 + 2] * LUMA_BLUE;
        const luminanceWeight = rangeMask?.LumRange != null
            ? smoothstep(lowSoft, low, luma) * (1 - smoothstep(high, highSoft, luma))
            : 1;
        result[i] = colorWeight * luminanceWeight;
    }
    return result;
}

function makeCanvas(width, height) {
    if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height);
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height; return canvas;
}

async function imageUrlPixels(url, width, height) {
    const response = await fetch(url);
    if (!response.ok) return null;
    const bitmap = await createImageBitmap(await response.blob());
    const canvas = makeCanvas(width, height), context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, width, height); bitmap.close?.();
    return context.getImageData(0, 0, width, height);
}

export async function loadAiRaster(mask, width, height, loader = null) {
    const loaded = loader ? await loader(mask, width, height) : await imageUrlPixels(`/api/develop/ai-mask/${encodeURIComponent(mask.pa_cache_key || '')}.png`, width, height);
    const result = new Float32Array(width * height);
    if (!loaded) return result;
    const data = loaded.data || loaded, channels = data.length / result.length, scale = data instanceof Uint8Array || data instanceof Uint8ClampedArray ? 1 / 255 : 1;
    for (let i = 0; i < result.length; i += 1) result[i] = clamp(data[i * channels] * scale);
    return result;
}

export async function rasterizeMask(mask, width, height, { image = null, grid = normalizedGrid(width, height), aiLoader = null } = {}) {
    if (Array.isArray(mask?.Masks)) {
        const result = new Float32Array(width * height);
        for (const primitive of mask.Masks) {
            const part = await rasterizeMask(primitive, width, height, { image, grid, aiLoader });
            for (let i = 0; i < result.length; i += 1) result[i] = Math.max(result[i], part[i]);
        }
        return result;
    }
    const what = String(mask?.What || mask?.MaskType || '');
    const rangeMask = mask?.CorrectionRangeMask || mask || {};
    if (/Circular|Radial/i.test(what)) return rasterizeRadial(mask, width, height, grid);
    if (/Gradient/i.test(what)) return rasterizeGradient(mask, width, height, grid);
    if (/Paint|Brush/i.test(what) || mask?.Dabs != null) return rasterizeBrush(mask, width, height, grid);
    if (/Image/i.test(what) || mask?.pa_cache_key != null) return loadAiRaster(mask, width, height, aiLoader);
    const type = number(rangeMask, 'Type');
    if (type === 1 || rangeMask.LumRange != null || /Luminance/i.test(what)) return rasterizeLuminanceRange(rangeMask, image, width, height);
    if (type === 2 || sampledColors(rangeMask).length || /Color/i.test(what)) return rasterizeColorRange(rangeMask, image, width, height);
    return new Float32Array(width * height);
}

export async function rasterizeCorrection(correction, width, height, { image = null, downsample = LOCAL_MASK_DOWNSAMPLE, aiLoader = null } = {}) {
    const [rasterWidth, rasterHeight] = rasterSize(width, height, downsample);
    const result = new Float32Array(rasterWidth * rasterHeight);
    if (!truth(correction?.CorrectionActive, true)) return { data: result, width: rasterWidth, height: rasterHeight };
    const grid = normalizedGrid(rasterWidth, rasterHeight, { regionWidth: width, regionHeight: height, canvasWidth: width, canvasHeight: height });
    for (const mask of correction?.CorrectionMasks || []) {
        if (!truth(mask?.MaskActive, true)) continue;
        const part = await rasterizeMask(mask, rasterWidth, rasterHeight, { image, grid, aiLoader });
        const opacity = clamp(number(mask, 'MaskValue', 1));
        for (let i = 0; i < result.length; i += 1) {
            const value = (truth(mask.MaskInverted) ? 1 - part[i] : part[i]) * opacity;
            result[i] = number(mask, 'MaskBlendMode') === 1 ? result[i] * value : Math.max(result[i], value);
        }
    }
    const rangeMask = correction?.CorrectionRangeMask;
    if (rangeMask && [1, 2].includes(number(rangeMask, 'Type'))) {
        const part = await rasterizeMask(rangeMask, rasterWidth, rasterHeight, { image, grid, aiLoader });
        const empty = !result.some((value) => value !== 0);
        for (let i = 0; i < result.length; i += 1) result[i] = empty ? part[i] : result[i] * part[i];
    }
    // CorrectionAmount is applied as a GL uniform so R8 atlas storage retains
    // precision while still allowing effective mask strengths through 2.0.
    return { data: result, width: rasterWidth, height: rasterHeight };
}

export function rasterToImageData(raster) {
    const data = new Uint8ClampedArray(raster.width * raster.height * 4);
    for (let i = 0; i < raster.data.length; i += 1) {
        const value = Math.round(clamp(raster.data[i]) * 255);
        data[i * 4] = value; data[i * 4 + 1] = value; data[i * 4 + 2] = value; data[i * 4 + 3] = 255;
    }
    return typeof ImageData !== 'undefined' ? new ImageData(data, raster.width, raster.height) : { data, width: raster.width, height: raster.height };
}

function needsRangeImage(corrections) {
    return corrections.some((correction) => (correction?.CorrectionMasks || []).some((mask) => {
        const rangeMask = mask.CorrectionRangeMask || mask;
        return [1, 2].includes(number(rangeMask, 'Type')) || rangeMask.LumRange != null || sampledColors(rangeMask).length;
    }));
}

/** Live UI entrypoint consumed by masking.js. */
export async function buildMaskRasters({ imageId = null, corrections = [], width, height }) {
    const limited = corrections.slice(0, LOCAL_RENDER_CAP);
    if (corrections.length > LOCAL_RENDER_CAP) console.warn(`Rendering the first ${LOCAL_RENDER_CAP} of ${corrections.length} local corrections`);
    const [rasterWidth, rasterHeight] = rasterSize(width, height);
    const image = imageId != null && needsRangeImage(limited) ? await imageUrlPixels(`/api/develop/${imageId}/base.jpg`, rasterWidth, rasterHeight) : null;
    const aiLoader = async (mask, maskWidth, maskHeight) => {
        let key = mask.pa_cache_key;
        if (!key && imageId != null && mask.MaskSubType != null) {
            const kind = String(mask.MaskSubType) === '2' ? 'sky' : 'subject';
            const response = await fetch(`/api/develop/${imageId}/ai-mask`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }),
            });
            if (!response.ok) return null;
            const generated = await response.json();
            key = generated.cache_key;
            if (key) mask.pa_cache_key = key; // Canonical JSON learns the lazy replacement.
        }
        return key ? imageUrlPixels(`/api/develop/ai-mask/${encodeURIComponent(key)}.png`, maskWidth, maskHeight) : null;
    };
    return Promise.all(limited.map(async (correction, correctionIndex) => ({
        correctionIndex,
        canvasOrImageData: rasterToImageData(await rasterizeCorrection(correction, width, height, { image, aiLoader })),
    })));
}

export const buildRasters = buildMaskRasters;
export default buildMaskRasters;
