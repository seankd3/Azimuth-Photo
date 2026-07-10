// Film emulation — JS/GL twin of features/develop/film.py (keep identical).
// buildFilmTables() precomputes the per-pixel path (two mat3s + two LUTs);
// the GLSL chunk evaluates it with halation from a dedicated wide blur field
// and clumped value-noise grain. Spatial params mirror the numpy stage.

export const FILM_LOGE_MIN = -3.0;
export const FILM_LOGE_MAX = 3.0;
export const FILM_LUT_SIZE = 256;
export const FILM_MID_GRAY = 0.18;

function interpCurve(points, x) {
    const pts = [...points].sort((a, b) => a[0] - b[0]);
    if (x <= pts[0][0]) return pts[0][1];
    for (let i = 1; i < pts.length; i++) {
        if (x <= pts[i][0]) {
            const [x0, y0] = pts[i - 1];
            const [x1, y1] = pts[i];
            const t = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
            return y0 + (y1 - y0) * t;
        }
    }
    return pts[pts.length - 1][1];
}

const mat3 = (rows) => rows.map((r) => r.map(Number));
const matVec = (m, v) => [0, 1, 2].map((i) => m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2]);

export function buildFilmTables(stock) {
    const bw = Object.keys(stock.hd_curves || {}).join(",") === "pan";
    const xs = new Array(FILM_LUT_SIZE).fill(0).map((_, i) =>
        FILM_LOGE_MIN + (FILM_LOGE_MAX - FILM_LOGE_MIN) * (i / (FILM_LUT_SIZE - 1)));
    const hd = new Float32Array(FILM_LUT_SIZE * 3);
    const channels = bw ? ["pan", "pan", "pan"] : ["r", "g", "b"];
    channels.forEach((ch, c) => {
        xs.forEach((x, i) => { hd[i * 3 + c] = interpCurve(stock.hd_curves[ch], x); });
    });

    let crosstalk = stock.spectral_crosstalk ? mat3(stock.spectral_crosstalk.length === 1
        ? [stock.spectral_crosstalk[0], stock.spectral_crosstalk[0], stock.spectral_crosstalk[0]]
        : stock.spectral_crosstalk) : mat3([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
    const dir = stock.dir_coupler && stock.dir_coupler.length === 3
        ? mat3(stock.dir_coupler) : mat3([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);

    const negative = String(stock.type || "").startsWith("negative") || bw;
    const base = stock.base || {};
    const paper = stock.print_paper || {};
    const paperGamma = Number(paper.gamma ?? 2.6);
    const paperShoulder = Math.max(Number(paper.shoulder ?? 0.92), 0.4);
    const mask = (base.orange_mask_rgb || [0, 0, 0]).map(Number);

    // per-channel print calibration at the speed point (twin of film.py)
    const speedIdx = Math.round((0 - FILM_LOGE_MIN) / (FILM_LOGE_MAX - FILM_LOGE_MIN) * (FILM_LUT_SIZE - 1));
    const dSpeed = matVec(dir, [hd[speedIdx * 3], hd[speedIdx * 3 + 1], hd[speedIdx * 3 + 2]]);
    const dRef = dSpeed.map((v, i) => v + (negative ? mask[i] : 0));

    const printLut = new Float32Array(FILM_LUT_SIZE);
    for (let i = 0; i < FILM_LUT_SIZE; i++) {
        const rel = -2.2 + 4.4 * (i / (FILM_LUT_SIZE - 1));
        let pos;
        if (negative) {
            const p = 0.18 * Math.pow(10, rel * paperGamma);
            const paperWhite = 2.0;
            let soft = p * (1 + p / (paperWhite * paperWhite)) / (1 + p);
            soft = Math.pow(Math.min(Math.max(soft, 0), 1), paperShoulder);
            pos = Math.min(Math.max(soft, 0), 1);
        } else {
            const dRefMean = (dRef[0] + dRef[1] + dRef[2]) / 3;
            let p = Math.pow(10, -(rel + dRefMean));
            p = Math.min(Math.max(p / Math.max(Math.pow(10, -dRefMean) / 0.18, 1e-6) * 0.18, 0), 1);
            pos = p;
        }
        printLut[i] = pos <= 0.0031308 ? pos * 12.92 : 1.055 * Math.pow(pos, 1 / 2.4) - 0.055;
    }

    const scan = stock.scan_matrix && stock.scan_matrix.length === 3
        ? mat3(stock.scan_matrix) : mat3([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
    const halation = stock.halation || {};
    const grain = stock.grain || {};
    return {
        hdLut: hd, printLut,
        crosstalk, dir, scan,
        mask: new Float32Array(mask), dRef: new Float32Array(dRef),
        negative, bw,
        halation: {
            amount: Number(halation.amount ?? 0),
            threshold: Number(halation.threshold ?? 0.7),
            radiusFrac: Number(halation.radius_frac ?? 0.015),
            greenFraction: Number(halation.green_fraction ?? 0.2),
        },
        grain: {
            rms: Number(grain.rms_granularity ?? grain.rms ?? 5),
            sizePxAt4k: Number(grain.size_px_at_4k ?? 3),
            shadowBias: Number(grain.shadow_bias ?? 0.35),
        },
    };
}

// GLSL chunk: expects the integrating pass to provide
//   uniform sampler2D u_filmHd;      // 256x1 RGB: logE→density per layer
//   uniform sampler2D u_filmPrint;   // 256x1 R:   rel-density→display
//   uniform mat3 u_filmCrosstalk, u_filmDir, u_filmScan;
//   uniform vec3 u_filmMask, u_filmDRef;
//   uniform vec4 u_filmHalation;     // amount, threshold, radiusFrac(unused in-shader), greenFraction
//   uniform vec3 u_filmGrain;        // sigmaD, cellPx, shadowBias
//   uniform float u_filmStrength; uniform bool u_filmBw, u_filmNegative;
//   float filmGlow — sampled from the wide halation blur field by the caller.
export const FILM_GLSL = `
float filmHash(vec2 p, float seed) {
    return fract(sin(dot(p, vec2(127.1, 311.7)) + seed * 91.17) * 43758.5453);
}

float filmValueNoise(vec2 uv, float seed) {
    vec2 i = floor(uv), f = fract(uv);
    f = f * f * (3.0 - 2.0 * f);
    float a = filmHash(i, seed), b = filmHash(i + vec2(1, 0), seed);
    float c = filmHash(i + vec2(0, 1), seed), d = filmHash(i + vec2(1, 1), seed);
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y) * 2.0 - 1.0;
}

vec3 filmTransform(vec3 linearRgb, float glow, vec2 fragPx, float dimMin) {
    vec3 layer = u_filmCrosstalk * max(linearRgb, 0.0);
    layer.r += u_filmHalation.x * glow;
    layer.g += u_filmHalation.x * u_filmHalation.w * glow;
    vec3 loge = log(max(layer / ${FILM_MID_GRAY}, 1e-6)) / log(10.0);
    vec3 u = clamp((loge - (${FILM_LOGE_MIN})) / (${FILM_LOGE_MAX} - (${FILM_LOGE_MIN})), 0.0, 1.0);
    vec3 density = vec3(
        texture(u_filmHd, vec2(u.r, 0.5)).r,
        texture(u_filmHd, vec2(u.g, 0.5)).g,
        texture(u_filmHd, vec2(u.b, 0.5)).b);
    density = u_filmDir * density;
    if (u_filmGrain.x > 0.0) {
        vec3 dNorm = clamp(density / 2.2, 0.0, 1.0);
        vec3 response = 4.0 * dNorm * (1.0 - dNorm);
        response = response * (1.0 - u_filmGrain.z) + u_filmGrain.z * (1.0 - dNorm);
        vec2 cellUv = fragPx / max(u_filmGrain.y, 1.0);
        vec3 noise = vec3(
            filmValueNoise(cellUv, 0.0),
            filmValueNoise(cellUv, 17.0),
            filmValueNoise(cellUv, 41.0));
        density += u_filmGrain.x * response * noise;
        if (u_filmBw) density = vec3(density.r);
    }
    if (u_filmNegative) density += u_filmMask;
    vec3 rel = density - u_filmDRef;
    vec3 pidx = clamp((rel + 2.2) / 4.4, 0.0, 1.0);
    vec3 positive = vec3(
        texture(u_filmPrint, vec2(pidx.r, 0.5)).r,
        texture(u_filmPrint, vec2(pidx.g, 0.5)).r,
        texture(u_filmPrint, vec2(pidx.b, 0.5)).r);
    vec3 outRgb = u_filmScan * positive;
    if (u_filmBw) outRgb = vec3(dot(outRgb, vec3(1.0 / 3.0)));
    return clamp(outRgb, 0.0, 1.0);
}
`;
