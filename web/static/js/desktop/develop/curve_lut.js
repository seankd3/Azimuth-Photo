import { BASE_PROFILE_POINTS, CAMERA_PROFILE_TONE_NODES } from './ops_constants.js';

const SIZE = 256;

function parsePoint(value) {
    if (Array.isArray(value)) return [Number(value[0]), Number(value[1])];
    const parts = String(value).split(',');
    return [Number(parts[0]), Number(parts[1])];
}

export function normalizeCurve(points) {
    const clean = (Array.isArray(points) ? points : [])
        .map(parsePoint)
        .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
        .map(([x, y]) => [Math.max(0, Math.min(255, x)), Math.max(0, Math.min(255, y))])
        .sort((a, b) => a[0] - b[0]);
    const unique = [];
    for (const point of clean) {
        if (unique.length && Math.abs(unique[unique.length - 1][0] - point[0]) < 1e-6) unique[unique.length - 1] = point;
        else unique.push(point);
    }
    return unique;
}

// Fritsch-Carlson monotone cubic interpolation. This is intentionally mirrored
// by the Python renderer; changing it is a renderer-parity change.
export function buildCurveLut(points) {
    const p = normalizeCurve(points).map(([x, y]) => [x / 255, y / 255]);
    const n = p.length;
    if (n < 2) return Float32Array.from({ length: SIZE }, (_, index) => index / (SIZE - 1));
    const delta = new Float64Array(Math.max(1, n - 1));
    const tangent = new Float64Array(n);
    for (let i = 0; i < n - 1; i += 1) {
        delta[i] = (p[i + 1][1] - p[i][1]) / Math.max(1e-9, p[i + 1][0] - p[i][0]);
    }
    tangent[0] = delta[0];
    tangent[n - 1] = delta[n - 2];
    for (let i = 1; i < n - 1; i += 1) {
        const left = delta[i - 1];
        const right = delta[i];
        if (left * right <= 0) tangent[i] = 0;
        else {
            const hLeft = p[i][0] - p[i - 1][0];
            const hRight = p[i + 1][0] - p[i][0];
            tangent[i] = (hLeft + hRight) / (hRight / left + hLeft / right);
        }
    }
    for (let i = 0; i < n - 1; i += 1) {
        if (Math.abs(delta[i]) < 1e-12) {
            tangent[i] = 0;
            tangent[i + 1] = 0;
            continue;
        }
        const a = tangent[i] / delta[i];
        const b = tangent[i + 1] / delta[i];
        const magnitude = Math.hypot(a, b);
        if (magnitude > 3) {
            const scale = 3 / magnitude;
            tangent[i] = scale * a * delta[i];
            tangent[i + 1] = scale * b * delta[i];
        }
    }
    const lut = new Float32Array(SIZE);
    let segment = 0;
    for (let i = 0; i < SIZE; i += 1) {
        const x = i / (SIZE - 1);
        while (segment < n - 2 && x > p[segment + 1][0]) segment += 1;
        const [x0, y0] = p[segment];
        const [x1, y1] = p[segment + 1];
        const width = Math.max(1e-9, x1 - x0);
        const t = Math.max(0, Math.min(1, (x - x0) / width));
        const t2 = t * t;
        const t3 = t2 * t;
        const y = (2 * t3 - 3 * t2 + 1) * y0
            + (t3 - 2 * t2 + t) * width * tangent[segment]
            + (-2 * t3 + 3 * t2) * y1
            + (t3 - t2) * width * tangent[segment + 1];
        lut[i] = Math.max(0, Math.min(1, y));
    }
    return lut;
}

function sampleLut(lut, value) {
    const position = Math.max(0, Math.min(1, Number(value))) * (SIZE - 1);
    const left = Math.floor(position);
    const right = Math.min(SIZE - 1, left + 1);
    const mix = position - left;
    return lut[left] + (lut[right] - lut[left]) * mix;
}

export function composeCurveLuts(baseLut, lookLut, amount = 1) {
    if (baseLut?.length !== SIZE || lookLut?.length !== SIZE) {
        throw new Error(`curve LUTs must both have ${SIZE} entries`);
    }
    const strength = Math.max(0, Math.min(1, Number(amount) || 0));
    return Float32Array.from(baseLut, value => value + (sampleLut(lookLut, value) - value) * strength);
}

export function lookAmount(settings = {}) {
    const look = settings?.Look;
    if (!look || typeof look !== 'object' || Array.isArray(look)) return 0;
    let amount = Number(look.Amount ?? 1);
    if (!Number.isFinite(amount)) amount = 1;
    if (Math.abs(amount) > 1) amount /= 100;
    return Math.max(0, Math.min(1, amount));
}

export function effectiveLookSettings(settings = {}) {
    const effective = { ...(settings || {}) };
    const look = settings?.Look;
    const parameters = look && typeof look === 'object' && !Array.isArray(look)
        && look.Parameters && typeof look.Parameters === 'object' && !Array.isArray(look.Parameters)
        ? look.Parameters : {};
    const amount = lookAmount(settings);
    if (amount <= 0) return effective;
    if (parameters.Clarity2012 != null) {
        const base = Number(settings.Clarity2012) || 0;
        const lookClarity = Number(parameters.Clarity2012) || 0;
        effective.Clarity2012 = Math.max(-100, Math.min(100, base + lookClarity * amount));
    }
    if (parameters.ConvertToGrayscale === true || parameters.ConvertToGrayscale === 1
        || String(parameters.ConvertToGrayscale).toLowerCase() === 'true') {
        effective.ConvertToGrayscale = true;
    }
    return effective;
}

export function buildBaseProfileLut(profile = null, settings = {}, baseKind = 'raw') {
    const nodes = profile?.tone_nodes;
    const values = profile?.tone_values;
    let base;
    if (baseKind === 'display') {
        base = buildCurveLut(null);
    } else if (Array.isArray(nodes) && nodes.length === CAMERA_PROFILE_TONE_NODES
        && Array.isArray(values) && values.length === CAMERA_PROFILE_TONE_NODES) {
        base = buildCurveLut(nodes.map((node, index) => [Number(node) * 255, Number(values[index]) * 255]));
    } else {
        base = buildCurveLut(BASE_PROFILE_POINTS);
    }
    const parameters = settings?.Look?.Parameters;
    const curve = parameters && typeof parameters === 'object' ? parameters.ToneCurvePV2012 : null;
    return curve ? composeCurveLuts(base, buildCurveLut(curve), lookAmount(settings)) : base;
}

export function buildCombinedCurveTexture(settings = {}) {
    const curves = [
        buildCurveLut(settings.ToneCurvePV2012),
        buildCurveLut(settings.ToneCurvePV2012Red),
        buildCurveLut(settings.ToneCurvePV2012Green),
        buildCurveLut(settings.ToneCurvePV2012Blue),
    ];
    const data = new Float32Array(SIZE * 4);
    for (let i = 0; i < SIZE; i += 1) {
        data[i * 4] = curves[0][i];
        data[i * 4 + 1] = curves[1][i];
        data[i * 4 + 2] = curves[2][i];
        data[i * 4 + 3] = curves[3][i];
    }
    return data;
}
