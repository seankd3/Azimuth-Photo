/** Grey guided-filter twin of features/develop/guided_filter.py. */
import {
    GAUSSIAN_TRUNCATE, GUIDED_DEFAULT_FEATHER, GUIDED_EPSILON_DEFAULT,
    GUIDED_FAST_MIN_SIDE, GUIDED_FAST_SUBSAMPLE, GUIDED_FEATHERING_MIN,
    GUIDED_FEATHERING_RANGE, GUIDED_GAUSSIAN_SIGMA_SCALE, GUIDED_RADIUS_FRACTION,
    LOCAL_RANGE_EPSILON, LUMA_BLUE, LUMA_GREEN, LUMA_RED,
} from './ops_constants.js';

function clamp01(value) {
    return Math.min(Math.max(value, 0), 1);
}

function downsample(field, width, height, subsample) {
    const smallW = Math.floor(width / subsample);
    const smallH = Math.floor(height / subsample);
    if (smallW < 1 || smallH < 1) {
        const outW = Math.max(1, Math.ceil(width / subsample));
        const outH = Math.max(1, Math.ceil(height / subsample));
        const out = new Float32Array(outW * outH);
        for (let y = 0; y < outH; y += 1) for (let x = 0; x < outW; x += 1) {
            out[y * outW + x] = field[Math.min(y * subsample, height - 1) * width + Math.min(x * subsample, width - 1)];
        }
        return { data: out, width: outW, height: outH };
    }
    const out = new Float32Array(smallW * smallH);
    const area = subsample * subsample;
    for (let y = 0; y < smallH; y += 1) {
        for (let x = 0; x < smallW; x += 1) {
            let sum = 0;
            const y0 = y * subsample;
            const x0 = x * subsample;
            for (let dy = 0; dy < subsample; dy += 1) for (let dx = 0; dx < subsample; dx += 1) {
                sum += field[(y0 + dy) * width + (x0 + dx)];
            }
            out[y * smallW + x] = sum / area;
        }
    }
    return { data: out, width: smallW, height: smallH };
}

function upsample(field, srcWidth, srcHeight, width, height) {
    const out = new Float32Array(width * height);
    for (let y = 0; y < height; y += 1) {
        const sy = Math.min(((y * srcHeight) / height) | 0, srcHeight - 1);
        for (let x = 0; x < width; x += 1) {
            const sx = Math.min(((x * srcWidth) / width) | 0, srcWidth - 1);
            out[y * width + x] = field[sy * srcWidth + sx];
        }
    }
    return out;
}

export function boxMean(values, width, height, radius) {
    if (radius <= 0) return Float32Array.from(values);
    const span = radius * 2 + 1;
    const paddedWidth = width + radius * 2;
    const paddedHeight = height + radius * 2;
    const padded = new Float32Array((paddedWidth + 1) * (paddedHeight + 1));
    for (let y = 0; y < paddedHeight; y += 1) {
        const sy = Math.min(Math.max(y - radius, 0), height - 1);
        for (let x = 0; x < paddedWidth; x += 1) {
            const sx = Math.min(Math.max(x - radius, 0), width - 1);
            padded[(y + 1) * (paddedWidth + 1) + (x + 1)] = values[sy * width + sx];
        }
    }
    for (let y = 1; y <= paddedHeight; y += 1) {
        for (let x = 1; x <= paddedWidth; x += 1) {
            const i = y * (paddedWidth + 1) + x;
            padded[i] += padded[i - 1] + padded[i - (paddedWidth + 1)] - padded[i - (paddedWidth + 1) - 1];
        }
    }
    const out = new Float32Array(width * height);
    const area = span * span;
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            const x0 = x;
            const y0 = y;
            const x1 = x + span;
            const y1 = y + span;
            const stride = paddedWidth + 1;
            out[y * width + x] = (
                padded[y1 * stride + x1] - padded[y0 * stride + x1]
                - padded[y1 * stride + x0] + padded[y0 * stride + x0]
            ) / area;
        }
    }
    return out;
}

function guidedCoefficients(guide, source, width, height, radius, epsilon) {
    const meanI = boxMean(guide, width, height, radius);
    const meanP = boxMean(source, width, height, radius);
    const corrII = new Float32Array(width * height);
    const corrIP = new Float32Array(width * height);
    for (let i = 0; i < corrII.length; i += 1) {
        corrII[i] = guide[i] * guide[i];
        corrIP[i] = guide[i] * source[i];
    }
    const meanII = boxMean(corrII, width, height, radius);
    const meanIP = boxMean(corrIP, width, height, radius);
    const a = new Float32Array(width * height);
    const b = new Float32Array(width * height);
    for (let i = 0; i < a.length; i += 1) {
        const variance = meanII[i] - meanI[i] * meanI[i];
        const covariance = meanIP[i] - meanI[i] * meanP[i];
        a[i] = covariance / (variance + epsilon);
        b[i] = meanP[i] - a[i] * meanI[i];
    }
    return [boxMean(a, width, height, radius), boxMean(b, width, height, radius)];
}

export function guidedFilter(guide, source, width, height, radius, epsilon = GUIDED_EPSILON_DEFAULT, { fast = true } = {}) {
    const r = Math.max(0, radius | 0);
    const eps = Math.max(Number(epsilon) || GUIDED_EPSILON_DEFAULT, LOCAL_RANGE_EPSILON);
    const subsample = GUIDED_FAST_SUBSAMPLE | 0;
    if (fast && subsample > 1 && Math.min(width, height) >= GUIDED_FAST_MIN_SIDE && r >= subsample) {
        const smallRadius = Math.max(1, (r / subsample) | 0);
        const smallGuide = downsample(guide, width, height, subsample);
        const smallSource = downsample(source, width, height, subsample);
        let [meanA, meanB] = guidedCoefficients(
            smallGuide.data, smallSource.data, smallGuide.width, smallGuide.height, smallRadius, eps,
        );
        meanA = boxMean(upsample(meanA, smallGuide.width, smallGuide.height, width, height), width, height, Math.max(1, (subsample / 2) | 0));
        meanB = boxMean(upsample(meanB, smallGuide.width, smallGuide.height, width, height), width, height, Math.max(1, (subsample / 2) | 0));
        const out = new Float32Array(width * height);
        for (let i = 0; i < out.length; i += 1) out[i] = meanA[i] * guide[i] + meanB[i];
        return out;
    }
    const [meanA, meanB] = guidedCoefficients(guide, source, width, height, r, eps);
    const out = new Float32Array(width * height);
    for (let i = 0; i < out.length; i += 1) out[i] = meanA[i] * guide[i] + meanB[i];
    return out;
}

export function featherToGuidedParams(feather, width, height) {
    const amount = clamp01(Number(feather) || 0);
    const minSide = Math.max(1, Math.min(width | 0, height | 0));
    const radius = Math.max(1, Math.round(amount * minSide * GUIDED_RADIUS_FRACTION));
    const feathering = GUIDED_FEATHERING_MIN + amount * GUIDED_FEATHERING_RANGE;
    return [radius, 1 / Math.max(feathering, LOCAL_RANGE_EPSILON)];
}

function gaussianSoft(field, width, height, sigma) {
    if (!(sigma > 0)) return Float32Array.from(field);
    const radius = Math.max(1, Math.ceil(GAUSSIAN_TRUNCATE * sigma));
    const kernel = new Float32Array(radius * 2 + 1);
    let sum = 0;
    for (let i = -radius; i <= radius; i += 1) {
        const value = Math.exp(-0.5 * (i / sigma) ** 2);
        kernel[i + radius] = value;
        sum += value;
    }
    for (let i = 0; i < kernel.length; i += 1) kernel[i] /= sum;
    const temp = new Float32Array(width * height);
    const out = new Float32Array(width * height);
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            let acc = 0;
            for (let k = -radius; k <= radius; k += 1) {
                const sx = Math.min(Math.max(x + k, 0), width - 1);
                acc += field[y * width + sx] * kernel[k + radius];
            }
            temp[y * width + x] = acc;
        }
    }
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            let acc = 0;
            for (let k = -radius; k <= radius; k += 1) {
                const sy = Math.min(Math.max(y + k, 0), height - 1);
                acc += temp[sy * width + x] * kernel[k + radius];
            }
            out[y * width + x] = acc;
        }
    }
    return out;
}

export function softMask(mask, width, height, radius, { guide = null, epsilon = GUIDED_EPSILON_DEFAULT } = {}) {
    if (!guide) {
        const sigma = Math.max(Number(radius) || 0, 0) * GUIDED_GAUSSIAN_SIGMA_SCALE;
        return gaussianSoft(mask, width, height, sigma);
    }
    const radiusI = Math.max(0, Math.ceil(Number(radius) || 0));
    if (radiusI <= 0) return Float32Array.from(mask);
    return guidedFilter(guide, mask, width, height, radiusI, epsilon);
}

export function refineMask(mask, guide, width, height, {
    feather = GUIDED_DEFAULT_FEATHER,
    radius = null,
    epsilon = null,
} = {}) {
    let mappedRadius = radius;
    let mappedEpsilon = epsilon;
    if (mappedRadius == null || mappedEpsilon == null) {
        const [r, e] = featherToGuidedParams(feather, width, height);
        if (mappedRadius == null) mappedRadius = r;
        if (mappedEpsilon == null) mappedEpsilon = e;
    }
    return softMask(mask, width, height, mappedRadius, { guide, epsilon: mappedEpsilon });
}

export function lumaGuide(rgb, width, height) {
    const out = new Float32Array(width * height);
    for (let i = 0; i < out.length; i += 1) {
        const o = i * 3;
        out[i] = rgb[o] * LUMA_RED + rgb[o + 1] * LUMA_GREEN + rgb[o + 2] * LUMA_BLUE;
    }
    return out;
}
