import {
    BAND_NAMES, BASE_PROFILE_SAT, BLUR_LARGE_FACTOR, BLUR_SMALL_FACTOR, CALIBRATION_HUE_MIX,
    CALIBRATION_SATURATION_SCALE, CALIBRATION_SHADOW_END, CALIBRATION_SHADOW_START,
    CALIBRATION_SHADOW_TINT_SCALE, CAMERA_PROFILE_BIN_CENTER,
    CAMERA_PROFILE_CHROMA_BINS, CAMERA_PROFILE_HUE_BINS, CAMERA_PROFILE_PI,
    CAMERA_PROFILE_TWO_PI, CLARITY_FACTOR, DNG_LINEAR_SRGB_TO_PROPHOTO, DNG_PROPHOTO_TO_LINEAR_SRGB,
    CLARITY_RESIDUAL_MAX, CONTRAST_FACTOR, DEHAZE_AIRLIGHT_FACTOR, DEHAZE_SATURATION_FACTOR,
    GRAIN_CELL_SIZE_MIN, GRAIN_CELL_SIZE_RANGE, GRAIN_FACTOR, GRAIN_FREQUENCY_MIN,
    GRAIN_FREQUENCY_RANGE, GRAIN_HASH_MULTIPLIER,
    GRAIN_HASH_SHIFT, GRAIN_OUTPUT_MASK, GRAIN_OUTPUT_SHIFT, GRAIN_SEED,
    GRAIN_X_MULTIPLIER, GRAIN_Y_MULTIPLIER, GRAY_MIXER_FACTOR, HSL_LUMINANCE_FACTOR,
    HUE_SHIFT_DEGREES, LENS_AUTO_CROP_EDGE_SAMPLES, LENS_IMAGE_CENTER, LENS_MANUAL_DISTORTION_FACTOR,
    LENS_MANUAL_VIGNETTE_FACTOR, LENS_MANUAL_VIGNETTE_MIDPOINT_MIN, LENS_MANUAL_VIGNETTE_MIDPOINT_RANGE,
    LENS_NORMALIZED_HALF_MIN, LENS_PROFILE_SCALE_DEFAULT, LENS_PROFILE_SCALE_MAX, LENS_PROFILE_SCALE_MIN,
    LENS_VIGNETTE_GAIN_MAX, LENS_VIGNETTE_GAIN_MIN, LUMA_BLUE, LUMA_GREEN, LUMA_RED, TINT_UV_SCALE,
    PERSPECTIVE_AMOUNT_SCALE, PERSPECTIVE_ASPECT_SCALE, PERSPECTIVE_OFFSET_SCALE,
    PERSPECTIVE_SCALE_BASE, PERSPECTIVE_SCALE_MIN,
    LOCAL_HUE_DEGREES, LOCAL_MASK_ATLAS_COLUMNS, LOCAL_RENDER_CAP,
    LOCAL_WB_TEMP_FACTOR, LOCAL_WB_TINT_FACTOR,
    OKLAB_C_NORM, OKLAB_M1, OKLAB_M1_INV, OKLAB_M2, OKLAB_M2_INV,
    SHARPEN_FACTOR, SHARPEN_THRESHOLD, SHARPEN_MASK_EDGE_LOW, SHARPEN_MASK_EDGE_HIGH, TEXTURE_FACTOR, TONE_BLACKS_FACTOR,
    TONE_EV_BLACKS_CENTER, TONE_EV_HIGHLIGHTS_CENTER, TONE_EV_SHADOWS_CENTER,
    TONE_EV_SIGMA, TONE_EV_WHITES_CENTER, TONE_HIGHLIGHTS_FACTOR,
    TONE_HIGHLIGHTS_POS_SCALE, TONE_SHADOWS_FACTOR, TONE_WHITES_FACTOR,
    VIBRANCE_FACTOR, VIGNETTE_FACTOR, VIGNETTE_FEATHER_MIN,
    VIGNETTE_FEATHER_RANGE, VIGNETTE_MIDPOINT_MIN, VIGNETTE_MIDPOINT_RANGE,
    VIGNETTE_ROUNDNESS_FACTOR, COLOR_GRADE_AB_SCALE, COLOR_GRADE_BALANCE_SHIFT,
    COLOR_GRADE_BLEND_MIN, COLOR_GRADE_BLEND_RANGE, COLOR_GRADE_HIGHLIGHT_CENTER,
    COLOR_GRADE_LUMINANCE_EV, COLOR_GRADE_SHADOW_CENTER, DEFRINGE_EDGE_HIGH,
    DEFRINGE_EDGE_LOW, DEFRINGE_HUE_SCALE, NR_LUMA_SIGMA, NR_LUMA_SPATIAL_AXIS,
    NR_LUMA_SPATIAL_CENTER, NR_DETAIL_EDGE_LOW, NR_DETAIL_EDGE_HIGH, NR_CONTRAST_RESIDUAL,
    RETOUCH_MIN_RADIUS, RETOUCH_RENDER_CAP, RETOUCH_RING_SCALE, RETOUCH_RING_TAPS,
    SOFT_PROOF_SRGB_TO_XYZ, SOFT_PROOF_XYZ_TO_SRGB, SOFT_PROOF_ADOBE_RGB_TO_XYZ,
    SOFT_PROOF_XYZ_TO_ADOBE_RGB, SOFT_PROOF_P3_TO_XYZ, SOFT_PROOF_XYZ_TO_P3,
    SOFT_PROOF_PAPER_WHITE, SOFT_PROOF_PAPER_BLACK, boolSetting, numberSetting,
} from './ops_constants.js';
import {
    buildBaseProfileLut, buildCombinedCurveTexture, effectiveLookSettings,
} from './curve_lut.js';
import { buildMaskRasters, localToSlider } from './mask_raster.js';
import { RETOUCH_SETTINGS_KEY } from './heal.js';
import { buildFilmTables, FILM_GLSL, FILM_LOGE_MAX, FILM_LOGE_MIN } from './film.js';
import { DNG_GLSL, packDngTable, packDngTone } from './dng_glsl.js';
import { markFrameDone } from './perf_overlay.js';
import { GEOMETRY_FRAGMENT } from './geometry_gl.js';

const FILM_STOCK_CACHE = new Map();

function fetchFilmStock(slug) {
    if (!FILM_STOCK_CACHE.has(slug)) {
        FILM_STOCK_CACHE.set(slug, fetch(`/api/develop/film/stocks/${encodeURIComponent(slug)}`, {
            headers: { Accept: 'application/json' },
        }).then((response) => {
            if (!response.ok) throw new Error(`Film stock ${slug} is unavailable`);
            return response.json();
        }));
    }
    return FILM_STOCK_CACHE.get(slug);
}

const matrixColumnMajor = (matrix) => new Float32Array([
    matrix[0][0], matrix[1][0], matrix[2][0],
    matrix[0][1], matrix[1][1], matrix[2][1],
    matrix[0][2], matrix[1][2], matrix[2][2],
]);

// JS stringifies integral Numbers without a decimal; GLSL then rejects the
// film chunk's vec3 ± scalar arithmetic. Keep the shipped chunk intact and
// normalize only its two interpolated log-exposure literals at integration.
const FILM_SHADER = FILM_GLSL
    .replaceAll(`(${FILM_LOGE_MIN})`, `(${Number(FILM_LOGE_MIN).toFixed(1)})`)
    .replaceAll(`(${FILM_LOGE_MAX} -`, `(${Number(FILM_LOGE_MAX).toFixed(1)} -`)
    .replace(' / log(10.0) + 1;', ' / log(10.0) + 1.0;');

/** Emit a GLSL float literal (JS 2.0 stringifies as "2", which GLSL treats as int). */
const f = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '0.0';
    const text = String(n);
    return /[eE.]/.test(text) ? text : `${text}.0`;
};

// Twin of lens.distortion_auto_crop_scale(): edge-evaluate the inverse radial
// polynomial, then zoom the output UV just enough to eliminate dark borders.
function lensAutoCropScale(distortion, width, height, cropRatio = 1, profileScale = 1) {
    if (!distortion || !(width > 0) || !(height > 0)) return 1;
    const terms = Array.isArray(distortion.terms) ? distortion.terms.map(Number) : [];
    const model = String(distortion.model || '').toLowerCase();
    const radial = (radius) => {
        const r2 = radius * radius;
        let scale = 1;
        if (model === 'poly3' && terms.length) scale = 1 - terms[0] + terms[0] * r2;
        else if (model === 'poly5' && terms.length >= 2) scale = 1 + terms[0] * r2 + terms[1] * r2 * r2;
        else if (model === 'ptlens' && terms.length >= 3) scale = terms[0] * radius * r2 + terms[1] * r2 + terms[2] * radius + 1 - terms[0] - terms[1] - terms[2];
        return 1 + (scale - 1) * profileScale;
    };
    const halfMin = Math.min(width, height) / LENS_NORMALIZED_HALF_MIN;
    let maximum = 1;
    for (let index = 0; index <= LENS_AUTO_CROP_EDGE_SAMPLES; index += 1) {
        const t = index / LENS_AUTO_CROP_EDGE_SAMPLES;
        for (const [u, v] of [[t, 0], [t, 1], [0, t], [1, t]]) {
            const px = (u * width - width * LENS_IMAGE_CENTER) / halfMin * cropRatio;
            const py = (v * height - height * LENS_IMAGE_CENTER) / halfMin * cropRatio;
            maximum = Math.max(maximum, radial(Math.hypot(px, py)));
        }
    }
    return Math.min(1, 1 / Math.max(maximum, 1e-6));
}

// §28: columns are source primaries. This exactly mirrors pipeline.calibration_matrix().
function calibrationMatrix(settings) {
    const basis = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const luma = [LUMA_RED, LUMA_GREEN, LUMA_BLUE];
    const columns = ['Red', 'Green', 'Blue'].map((name, index) => {
        const saturation = Math.max(-1, Math.min(1, numberSetting(settings, `Calibration${name}PrimarySaturation`) / 100)) * CALIBRATION_SATURATION_SCALE;
        const column = luma.map((value, row) => value + (1 + saturation) * (basis[index][row] - value));
        const hue = Math.max(-1, Math.min(1, numberSetting(settings, `Calibration${name}PrimaryHue`) / 100));
        if (hue !== 0) {
            const adjacent = (index + (hue > 0 ? 1 : 2)) % 3;
            for (let row = 0; row < 3; row += 1) column[row] += Math.abs(hue) * CALIBRATION_HUE_MIX * (basis[adjacent][row] - basis[index][row]);
        }
        return column;
    });
    return new Float32Array(columns.flat());
}

const correctionEnabled = (correction) => correction?.CorrectionActive == null
    || !['false', '0'].includes(String(correction.CorrectionActive).toLowerCase());

function retouchSpots(settings) {
    const values = settings?.[RETOUCH_SETTINGS_KEY];
    if (!Array.isArray(values)) return [];
    const unit = (value, fallback) => {
        const number = Number(value);
        return Math.max(0, Math.min(1, Number.isFinite(number) ? number : fallback));
    };
    const spots = [];
    for (const value of values.slice(0, RETOUCH_RENDER_CAP)) {
        if (!value || typeof value !== 'object') continue;
        const mode = String(value.mode || 'clone').toLowerCase();
        const rawRadius = Number(value.radius);
        if (!['clone', 'heal'].includes(mode) || !(rawRadius > 0)) continue;
        spots.push({
            src_x: unit(value.src_x ?? value.srcX, .5),
            src_y: unit(value.src_y ?? value.srcY, .5),
            dst_x: unit(value.dst_x ?? value.dstX, .5),
            dst_y: unit(value.dst_y ?? value.dstY, .5),
            radius: Math.max(RETOUCH_MIN_RADIUS, Math.min(.5, rawRadius)),
            feather: unit(value.feather, .5),
            opacity: unit(value.opacity, 1),
            mode,
        });
    }
    return spots;
}

const VERTEX = `#version 300 es
in vec2 a_position;
uniform vec2 u_viewCenter;
uniform float u_viewScale;
out vec2 v_uv;
void main() {
    v_uv = a_position * .5 + .5;
    vec2 imagePosition = vec2(v_uv.x, 1.0 - v_uv.y);
    vec2 viewed = (imagePosition - u_viewCenter) * u_viewScale + .5;
    gl_Position = vec4(viewed.x * 2.0 - 1.0, 1.0 - viewed.y * 2.0, 0.0, 1.0);
}`;

const COLOR_MATH = `
const vec3 LUMW = vec3(${f(LUMA_RED)}, ${f(LUMA_GREEN)}, ${f(LUMA_BLUE)});
const mat3 OKM1 = mat3(
    ${f(OKLAB_M1[0][0])}, ${f(OKLAB_M1[1][0])}, ${f(OKLAB_M1[2][0])},
    ${f(OKLAB_M1[0][1])}, ${f(OKLAB_M1[1][1])}, ${f(OKLAB_M1[2][1])},
    ${f(OKLAB_M1[0][2])}, ${f(OKLAB_M1[1][2])}, ${f(OKLAB_M1[2][2])}
);
const mat3 OKM2 = mat3(
    ${f(OKLAB_M2[0][0])}, ${f(OKLAB_M2[1][0])}, ${f(OKLAB_M2[2][0])},
    ${f(OKLAB_M2[0][1])}, ${f(OKLAB_M2[1][1])}, ${f(OKLAB_M2[2][1])},
    ${f(OKLAB_M2[0][2])}, ${f(OKLAB_M2[1][2])}, ${f(OKLAB_M2[2][2])}
);
const mat3 OKM1I = mat3(
    ${f(OKLAB_M1_INV[0][0])}, ${f(OKLAB_M1_INV[1][0])}, ${f(OKLAB_M1_INV[2][0])},
    ${f(OKLAB_M1_INV[0][1])}, ${f(OKLAB_M1_INV[1][1])}, ${f(OKLAB_M1_INV[2][1])},
    ${f(OKLAB_M1_INV[0][2])}, ${f(OKLAB_M1_INV[1][2])}, ${f(OKLAB_M1_INV[2][2])}
);
const mat3 OKM2I = mat3(
    ${f(OKLAB_M2_INV[0][0])}, ${f(OKLAB_M2_INV[1][0])}, ${f(OKLAB_M2_INV[2][0])},
    ${f(OKLAB_M2_INV[0][1])}, ${f(OKLAB_M2_INV[1][1])}, ${f(OKLAB_M2_INV[2][1])},
    ${f(OKLAB_M2_INV[0][2])}, ${f(OKLAB_M2_INV[1][2])}, ${f(OKLAB_M2_INV[2][2])}
);
float sat(float value) { return clamp(value, 0.0, 1.0); }
float setting(float value) { return value / 100.0; }
float gaussEv(float ev, float center) {
    float z = (ev - center) / ${f(TONE_EV_SIGMA)};
    return exp(-0.5 * z * z);
}
vec3 linearToSrgb(vec3 c) {
    bvec3 low = lessThanEqual(c, vec3(.0031308));
    vec3 lo = c * 12.92;
    vec3 hi = 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - .055;
    return mix(hi, lo, low);
}
vec3 srgbToLinear(vec3 c) {
    bvec3 low = lessThanEqual(c, vec3(.04045));
    vec3 lo = c / 12.92;
    vec3 hi = pow((c + .055) / 1.055, vec3(2.4));
    return mix(hi, lo, low);
}
vec3 linearToOklab(vec3 c) {
    vec3 lms = OKM1 * c;
    lms = sign(lms) * pow(abs(lms), vec3(1.0 / 3.0));
    return OKM2 * lms;
}
vec3 oklabToLinear(vec3 lab) {
    vec3 lms = OKM2I * lab;
    lms = lms * lms * lms;
    return OKM1I * lms;
}
vec3 gamutClipDesat(vec3 linear, vec3 lab) {
    if (all(greaterThanEqual(linear, vec3(0.0))) && all(lessThanEqual(linear, vec3(1.0)))) return clamp(linear, 0.0, 1.0);
    float lo = 0.0;
    float hi = 1.0;
    for (int i = 0; i < 8; i++) {
        float mid = 0.5 * (lo + hi);
        vec3 candidate = oklabToLinear(vec3(lab.x, lab.yz * mid));
        bool ok = all(greaterThanEqual(candidate, vec3(0.0))) && all(lessThanEqual(candidate, vec3(1.0)));
        if (ok) lo = mid; else hi = mid;
    }
    return clamp(oklabToLinear(vec3(lab.x, lab.yz * lo)), 0.0, 1.0);
}
vec3 scaleOklabChroma(vec3 srgb, float multiply, float saturation, float vibrance, float dehaze) {
    if (abs(multiply - 1.0) < 1e-6 && abs(saturation) < 1e-6 && abs(vibrance) < 1e-6 && abs(dehaze) < 1e-6) return srgb;
    vec3 linear = srgbToLinear(srgb);
    vec3 lab = linearToOklab(linear);
    float chroma = length(lab.yz);
    float satness = clamp(chroma / ${f(OKLAB_C_NORM)}, 0.0, 1.0);
    float scale = multiply * (1.0 + saturation + ${f(DEHAZE_SATURATION_FACTOR)} * dehaze);
    float chromaNew = max(chroma * scale + vibrance * (1.0 - satness) * satness * ${f(VIBRANCE_FACTOR)} * ${f(OKLAB_C_NORM)}, 0.0);
    if (chroma > 1e-6) lab.yz *= chromaNew / chroma;
    else lab.yz = vec2(0.0);
    return linearToSrgb(gamutClipDesat(oklabToLinear(lab), lab));
}
vec3 applyCameraProfileAb(vec3 srgb) {
    if (!u_cameraProfile) return srgb;
    vec3 lab = linearToOklab(srgbToLinear(srgb));
    float chroma = length(lab.yz);
    float huePosition = (atan(lab.z, lab.y) + ${f(CAMERA_PROFILE_PI)})
        / ${f(CAMERA_PROFILE_TWO_PI)} * ${f(CAMERA_PROFILE_HUE_BINS)} - ${f(CAMERA_PROFILE_BIN_CENTER)};
    float hueFloor = floor(huePosition);
    float hueMix = huePosition - hueFloor;
    int hue0 = int(mod(hueFloor, float(${CAMERA_PROFILE_HUE_BINS})));
    if (hue0 < 0) hue0 += ${CAMERA_PROFILE_HUE_BINS};
    int hue1 = (hue0 + 1) % ${CAMERA_PROFILE_HUE_BINS};
    vec3 centers = vec3(
        u_cameraChromaEdges[0] * ${f(CAMERA_PROFILE_BIN_CENTER)},
        (u_cameraChromaEdges[0] + u_cameraChromaEdges[1]) * ${f(CAMERA_PROFILE_BIN_CENTER)},
        (u_cameraChromaEdges[1] + u_cameraChromaEdges[2]) * ${f(CAMERA_PROFILE_BIN_CENTER)}
    );
    int chroma0 = chroma < centers.y ? 0 : 1;
    int chroma1 = min(chroma0 + 1, ${CAMERA_PROFILE_CHROMA_BINS - 1});
    float left = centers[chroma0];
    float right = centers[chroma1];
    float chromaMix = right > left ? clamp((chroma - left) / (right - left), 0.0, 1.0) : 0.0;
    vec2 delta0 = mix(
        u_cameraAbDelta[hue0 * ${CAMERA_PROFILE_CHROMA_BINS} + chroma0],
        u_cameraAbDelta[hue1 * ${CAMERA_PROFILE_CHROMA_BINS} + chroma0], hueMix
    );
    vec2 delta1 = mix(
        u_cameraAbDelta[hue0 * ${CAMERA_PROFILE_CHROMA_BINS} + chroma1],
        u_cameraAbDelta[hue1 * ${CAMERA_PROFILE_CHROMA_BINS} + chroma1], hueMix
    );
    lab.yz += mix(delta0, delta1, chromaMix);
    return linearToSrgb(gamutClipDesat(oklabToLinear(lab), lab));
}
vec3 rgbToHsv(vec3 c) {
    float maximum = max(c.r, max(c.g, c.b));
    float minimum = min(c.r, min(c.g, c.b));
    float delta = maximum - minimum;
    float hue = 0.0;
    if (delta > 1e-10) {
        if (maximum == c.r) hue = mod((c.g - c.b) / delta, 6.0);
        else if (maximum == c.g) hue = (c.b - c.r) / delta + 2.0;
        else hue = (c.r - c.g) / delta + 4.0;
        hue = mod(hue / 6.0, 1.0);
    }
    return vec3(hue, maximum > 0.0 ? delta / maximum : 0.0, maximum);
}
vec3 hsvToRgb(vec3 c) {
    float h = fract(c.x) * 6.0;
    float chroma = c.z * c.y;
    float x = chroma * (1.0 - abs(mod(h, 2.0) - 1.0));
    vec3 prime;
    if (h < 1.0) prime = vec3(chroma, x, 0.0);
    else if (h < 2.0) prime = vec3(x, chroma, 0.0);
    else if (h < 3.0) prime = vec3(0.0, chroma, x);
    else if (h < 4.0) prime = vec3(0.0, x, chroma);
    else if (h < 5.0) prime = vec3(x, 0.0, chroma);
    else prime = vec3(chroma, 0.0, x);
    return prime + vec3(c.z - chroma);
}
float hueDistance(float a, float b) { return abs(mod(a - b + 180.0, 360.0) - 180.0); }
float bandWeight(float hue, int index) {
    float centers[8] = float[8](0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 280.0, 320.0);
    float center = centers[index];
    float left = centers[(index + 7) % 8];
    float right = centers[(index + 1) % 8];
    float signedDelta = mod(hue - center + 540.0, 360.0) - 180.0;
    float span = signedDelta < 0.0 ? mod(center - left + 360.0, 360.0) : mod(right - center + 360.0, 360.0);
    float t = abs(signedDelta) / max(span, 1.0);
    return t < 1.0 ? .5 + .5 * cos(3.14159265359 * t) : 0.0;
}
`;

// Twin of wb_gains() in features/develop/pipeline.py — keep identical.
function cctToXy(cct) {
    const t = Math.min(Math.max(Number(cct), 1667), 25000);
    const inv = 1000 / t;
    const x = t < 4000
        ? ((-0.2661239 * inv - 0.2343589) * inv + 0.8776956) * inv + 0.179910
        : ((-3.0258469 * inv + 2.1070379) * inv + 0.2226347) * inv + 0.240390;
    let y;
    if (t < 2222) y = ((-1.1063814 * x - 1.34811020) * x + 2.18555832) * x - 0.20219683;
    else if (t < 4000) y = ((-0.9549476 * x - 1.37418593) * x + 2.09137015) * x - 0.16748867;
    else y = ((3.0817580 * x - 5.87338670) * x + 3.75112997) * x - 0.37001483;
    return [x, y];
}

function whiteLinearSrgb(cct, tint) {
    const [x, y] = cctToXy(cct);
    const d = -2 * x + 12 * y + 3;
    const u = 4 * x / d;
    const v = 9 * y / d + Number(tint) / TINT_UV_SCALE;
    const d2 = 6 * u - 16 * v + 12;
    const x2 = 9 * u / d2;
    const y2 = Math.max(4 * v / d2, 1e-6);
    const X = x2 / y2;
    const Z = (1 - x2 - y2) / y2;
    return [
        Math.max(3.2404542 * X - 1.5371385 - 0.4985314 * Z, 1e-4),
        Math.max(-0.9692660 * X + 1.8760108 + 0.0415560 * Z, 1e-4),
        Math.max(0.0556434 * X - 0.2040259 + 1.0572252 * Z, 1e-4),
    ];
}

export function wbGains(asShotTemperature, asShotTint, temperature, tint) {
    const wa = whiteLinearSrgb(asShotTemperature, asShotTint);
    const wu = whiteLinearSrgb(temperature, tint);
    return [0, 1, 2].map((i) => Math.min(Math.max((wa[i] / wa[1]) / (wu[i] / wu[1]), 0.125), 8));
}

// Production Develop responses carry the exact color payload used by
// render_display_preview().  Fall back to the historical metadata shape for
// standalone parity/debug callers that do not come through the API.
function canvasColorProfile(meta = {}) {
    const explicit = meta?.canvas_color_profile;
    return explicit && typeof explicit === 'object' && !Array.isArray(explicit) ? explicit : meta;
}

const XYZD50_TO_SRGB = [
    [3.1338561, -1.6168667, -0.4906146],
    [-0.9787684, 1.9161415, 0.0334540],
    [0.0719453, -0.2289914, 1.4052427],
];

function mat3Mul(a, b) {
    const out = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) out[i][j] += a[i][k] * b[k][j];
    return out;
}

function matrixMultiplyVector(matrix, vector) {
    return matrix.map((row) => row[0] * vector[0] + row[1] * vector[1] + row[2] * vector[2]);
}

function proofMatrices(profile) {
    if (profile === 'adobe-rgb') return [SOFT_PROOF_ADOBE_RGB_TO_XYZ, SOFT_PROOF_XYZ_TO_ADOBE_RGB];
    if (profile === 'display-p3') return [SOFT_PROOF_P3_TO_XYZ, SOFT_PROOF_XYZ_TO_P3];
    return [SOFT_PROOF_SRGB_TO_XYZ, SOFT_PROOF_XYZ_TO_SRGB];
}

// Twin of pipeline.soft_proof_transform(): clip in the target RGB space,
// then return to sRGB for display. Paper is a deliberately simple paper-point simulation.
export function softProofTransform(srgb, profile = 'srgb') {
    const linear = srgb.map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    const [toXyz, fromXyz] = proofMatrices(profile);
    const proof = matrixMultiplyVector(fromXyz, matrixMultiplyVector(SOFT_PROOF_SRGB_TO_XYZ, linear));
    const outOfGamut = proof.some((value) => value < 0 || value > 1);
    let displayed = matrixMultiplyVector(SOFT_PROOF_XYZ_TO_SRGB, matrixMultiplyVector(toXyz, proof.map((value) => Math.min(1, Math.max(0, value)))));
    if (profile === 'paper') displayed = displayed.map((value) => SOFT_PROOF_PAPER_BLACK + value * (SOFT_PROOF_PAPER_WHITE - SOFT_PROOF_PAPER_BLACK));
    return { srgb: displayed.map((value) => value <= .0031308 ? value * 12.92 : 1.055 * Math.max(0, value) ** (1 / 2.4) - .055), outOfGamut };
}

function mat3Inv(m) {
    const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
    const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
    const s = 1 / det;
    return [
        [(e * i - f * h) * s, (c * h - b * i) * s, (b * f - c * e) * s],
        [(f * g - d * i) * s, (a * i - c * g) * s, (c * d - a * f) * s],
        [(d * h - e * g) * s, (b * g - a * h) * s, (a * e - b * d) * s],
    ];
}

// JS twin of transform.inverse_homography(): projective -> rotate -> affine,
// then invert and flatten by columns for WebGL's column-major mat3 upload.
export function transformInverseColumnMajor(settings = {}) {
    const finite = (key, fallback) => {
        const value = Number(settings?.[key]);
        return Number.isFinite(value) ? value : fallback;
    };
    const bounded = (key, fallback, min, max) => Math.min(max, Math.max(min, finite(key, fallback)));
    const vertical = bounded('PerspectiveVertical', 0, -100, 100) * PERSPECTIVE_AMOUNT_SCALE;
    const horizontal = bounded('PerspectiveHorizontal', 0, -100, 100) * PERSPECTIVE_AMOUNT_SCALE;
    const angle = bounded('PerspectiveRotate', 0, -45, 45) * Math.PI / 180;
    const scale = Math.max(PERSPECTIVE_SCALE_MIN, finite('PerspectiveScale', PERSPECTIVE_SCALE_BASE)) / PERSPECTIVE_SCALE_BASE;
    const aspect = Math.max(
        PERSPECTIVE_SCALE_MIN / PERSPECTIVE_SCALE_BASE,
        1 + bounded('PerspectiveAspect', 0, -100, 100) * PERSPECTIVE_ASPECT_SCALE,
    );
    const offsetX = bounded('PerspectiveX', 0, -100, 100) * PERSPECTIVE_OFFSET_SCALE;
    const offsetY = bounded('PerspectiveY', 0, -100, 100) * PERSPECTIVE_OFFSET_SCALE;
    const projective = [[1, 0, 0], [0, 1, 0], [horizontal, vertical, 1]];
    const rotate = [
        [Math.cos(angle), -Math.sin(angle), 0],
        [Math.sin(angle), Math.cos(angle), 0],
        [0, 0, 1],
    ];
    const affine = [[scale * aspect, 0, offsetX], [0, scale, offsetY], [0, 0, 1]];
    const inverse = mat3Inv(mat3Mul(mat3Mul(affine, rotate), projective))
        || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    return new Float32Array([
        inverse[0][0], inverse[1][0], inverse[2][0],
        inverse[0][1], inverse[1][1], inverse[2][1],
        inverse[0][2], inverse[1][2], inverse[2][2],
    ]);
}

function xyFromCctTint(cct, tint) {
    const [x, y] = cctToXy(cct);
    const d = -2 * x + 12 * y + 3;
    const u = 4 * x / d;
    const v = 9 * y / d + Number(tint) / TINT_UV_SCALE;
    const d2 = 6 * u - 16 * v + 12;
    return [9 * u / d2, Math.max(4 * v / d2, 1e-6)];
}

// Twin of wb_matrix() in features/develop/pipeline.py — keep identical.
export function wbMatrix(color, asShotTemperature, asShotTint, temperature, tint) {
    if (!color || !color.as_shot_neutral || !color.forward_matrix) return null;
    const asn = color.as_shot_neutral.slice(0, 3).map(Number);
    if (asn.length < 3 || asn.some((v) => !(v > 0))) return null;
    const rows = (flat) => [flat.slice(0, 3), flat.slice(3, 6), flat.slice(6, 9)];
    const cm2 = color.color_matrix2 ? rows(color.color_matrix2.map(Number)) : null;
    const cm1 = color.color_matrix1 ? rows(color.color_matrix1.map(Number)) : null;
    if (!cm2 && !cm1) return null;
    const mired = 1e6 / Math.min(Math.max(Number(temperature), 2000), 50000);
    let cm;
    if (!cm1) cm = cm2;
    else if (!cm2) cm = cm1;
    else {
        let w = (mired - 1e6 / 6504) / (1e6 / 2856 - 1e6 / 6504);
        w = Math.min(Math.max(w, 0), 1);
        cm = cm2.map((row, i) => row.map((v, j) => v + w * (cm1[i][j] - v)));
    }
    const [x, y] = xyFromCctTint(Number(temperature), Number(tint));
    const xyz = [x / y, 1, (1 - x - y) / y];
    const nUser = cm.map((row) => row[0] * xyz[0] + row[1] * xyz[1] + row[2] * xyz[2]);
    if (nUser.some((v) => !Number.isFinite(v) || v <= 0)) return null;
    const ng = nUser[1];
    const gains = [0, 1, 2].map((i) => (asn[i] / asn[1]) / (nUser[i] / ng));
    const m = mat3Mul(XYZD50_TO_SRGB, rows(color.forward_matrix.map(Number)));
    const mInv = mat3Inv(m);
    if (!mInv) return null;
    const diag = [[gains[0], 0, 0], [0, gains[1], 0], [0, 0, gains[2]]];
    return mat3Mul(mat3Mul(m, diag), mInv);
}

const COLOR_UNIFORMS = `
uniform sampler2D u_source;
uniform sampler2D u_curve;
uniform sampler2D u_baseCurve;
uniform sampler2D u_filmHd;
uniform sampler2D u_filmPrint;
uniform sampler2D u_filmGlow;
uniform bool u_filmActive;
uniform bool u_filmBw;
uniform bool u_filmNegative;
uniform mat3 u_filmCrosstalk;
uniform mat3 u_filmDir;
uniform mat3 u_filmScan;
uniform vec3 u_filmMask;
uniform vec3 u_filmDRef;
uniform vec4 u_filmHalation;
uniform vec3 u_filmGrain;
uniform float u_filmStrength;
uniform vec2 u_sourceSize;
uniform float u_baseProfileSat;
uniform bool u_cameraProfile;
uniform vec2 u_cameraAbDelta[${CAMERA_PROFILE_HUE_BINS * CAMERA_PROFILE_CHROMA_BINS}];
uniform float u_cameraChromaEdges[${CAMERA_PROFILE_CHROMA_BINS + 1}];
uniform bool u_lensProfile;
uniform int u_lensModel;
uniform vec3 u_lensTerms;
uniform bool u_lensVignetting;
uniform vec3 u_lensVignetteTerms;
uniform float u_lensCropRatio;
uniform float u_lensAutoCrop;
uniform float u_lensDistortionScale;
uniform float u_lensVignettingScale;
uniform float u_lensManualDistortion;
uniform float u_lensManualVignette;
uniform float u_lensManualVignetteMidpoint;
uniform mat3 u_wbMatrix;
uniform bool u_applyWb;
uniform mat3 u_calibrationMatrix;
uniform float u_calibrationShadowTint;
uniform float u_exposure;
uniform float u_contrast;
uniform vec4 u_regions;
uniform float u_dehaze;
uniform float u_vibrance;
uniform float u_saturation;
uniform float u_hue[8];
uniform float u_hslSat[8];
uniform float u_hslLum[8];
uniform float u_gray[8];
uniform bool u_grayscale;
uniform bool u_useHsl;
uniform vec4 u_crop;
uniform float u_angle;
uniform int u_orientation;
uniform bool u_applyGeometry;
uniform mat3 u_transformInverse;
uniform vec3 u_gradeShadow;
uniform vec3 u_gradeMidtone;
uniform vec3 u_gradeHighlight;
uniform vec3 u_gradeGlobal;
uniform float u_gradeBlending;
uniform float u_gradeBalance;
uniform bool u_dngActive;
uniform bool u_dngHueActive;
uniform bool u_dngLookActive;
uniform sampler2D u_dngHueSat;
uniform sampler2D u_dngLook;
uniform sampler2D u_dngTone;
uniform ivec3 u_dngHueDims;
uniform ivec3 u_dngLookDims;
uniform int u_dngHueEncoding;
uniform int u_dngLookEncoding;
uniform mat3 u_dngSrgbToProPhoto;
uniform mat3 u_dngProPhotoToSrgb;
uniform float u_dngBaselineExposure;
`;

const COLOR_FUNCTION = `
float lensRadius(vec2 uv) {
    float halfMin = min(u_sourceSize.x, u_sourceSize.y) / ${f(LENS_NORMALIZED_HALF_MIN)};
    vec2 p = (uv * u_sourceSize - u_sourceSize * ${f(LENS_IMAGE_CENTER)}) / halfMin * u_lensCropRatio;
    return length(p);
}
float lensRadialScale(float radius) {
    float r2 = radius * radius;
    float profile = 1.0;
    if (u_lensModel == 1) profile = 1.0 - u_lensTerms.x + u_lensTerms.x * r2;
    else if (u_lensModel == 2) profile = 1.0 + u_lensTerms.x * r2 + u_lensTerms.y * r2 * r2;
    else if (u_lensModel == 3) profile = u_lensTerms.x * radius * r2 + u_lensTerms.y * r2
        + u_lensTerms.z * radius + 1.0 - u_lensTerms.x - u_lensTerms.y - u_lensTerms.z;
    return (1.0 + (profile - 1.0) * u_lensDistortionScale)
        * (1.0 + u_lensManualDistortion * ${f(LENS_MANUAL_DISTORTION_FACTOR)} * r2);
}
vec2 lensDistortedUv(vec2 uv) {
    if (!u_lensProfile && abs(u_lensManualDistortion) < 1e-5) return uv;
    float halfMin = min(u_sourceSize.x, u_sourceSize.y) / ${f(LENS_NORMALIZED_HALF_MIN)};
    vec2 p = (uv * u_sourceSize - u_sourceSize * ${f(LENS_IMAGE_CENTER)}) / halfMin * u_lensCropRatio;
    p *= lensRadialScale(length(p));
    return (u_sourceSize * ${f(LENS_IMAGE_CENTER)} + p / u_lensCropRatio * halfMin) / u_sourceSize;
}
float lensVignetteGain(vec2 sourceUv) {
    if (!u_lensVignetting && abs(u_lensManualVignette) < 1e-5) return 1.0;
    float halfMin = min(u_sourceSize.x, u_sourceSize.y) / ${f(LENS_NORMALIZED_HALF_MIN)};
    float cornerRadius = length((u_sourceSize * ${f(LENS_IMAGE_CENTER)}) / halfMin * u_lensCropRatio);
    float radius = lensRadius(sourceUv) / max(cornerRadius, 1e-6);
    float r2 = radius * radius;
    float polynomial = 1.0 + u_lensVignetteTerms.x * r2 + u_lensVignetteTerms.y * r2 * r2
        + u_lensVignetteTerms.z * r2 * r2 * r2;
    float profile = clamp(1.0 / max(polynomial, 1e-6),
        ${f(LENS_VIGNETTE_GAIN_MIN)}, ${f(LENS_VIGNETTE_GAIN_MAX)});
    float midpoint = ${f(LENS_MANUAL_VIGNETTE_MIDPOINT_MIN)}
        + clamp(u_lensManualVignetteMidpoint / 100.0, 0.0, 1.0) * ${f(LENS_MANUAL_VIGNETTE_MIDPOINT_RANGE)};
    float manual = 1.0 + u_lensManualVignette * ${f(LENS_MANUAL_VIGNETTE_FACTOR)}
        * smoothstep(midpoint, 1.0, clamp(radius, 0.0, 1.0));
    return max(0.0, (u_lensVignetting ? 1.0 + (profile - 1.0) * u_lensVignettingScale : 1.0) * manual);
}
vec2 lensUv(vec2 uv) {
    if (u_lensProfile) uv = vec2(.5) + (uv - vec2(.5)) * u_lensAutoCrop;
    return lensDistortedUv(uv);
}
float gradeBandWeight(float lightness, int band) {
    float width = ${f(COLOR_GRADE_BLEND_MIN)} + clamp(u_gradeBlending / 100.0, 0.0, 1.0) * ${f(COLOR_GRADE_BLEND_RANGE)};
    float shift = setting(u_gradeBalance) * ${f(COLOR_GRADE_BALANCE_SHIFT)};
    float shadows = 1.0 - smoothstep(${f(COLOR_GRADE_SHADOW_CENTER)} + shift - width, ${f(COLOR_GRADE_SHADOW_CENTER)} + shift + width, lightness);
    float highlights = smoothstep(${f(COLOR_GRADE_HIGHLIGHT_CENTER)} + shift - width, ${f(COLOR_GRADE_HIGHLIGHT_CENTER)} + shift + width, lightness);
    if (band == 0) return shadows;
    if (band == 1) return clamp(1.0 - max(shadows, highlights), 0.0, 1.0);
    if (band == 2) return highlights;
    return 1.0;
}
vec3 applyGrade(vec3 srgb) {
    vec3 wheels[4] = vec3[4](u_gradeShadow, u_gradeMidtone, u_gradeHighlight, u_gradeGlobal);
    float lightness = dot(srgb, LUMW);
    vec3 lab = linearToOklab(srgbToLinear(srgb));
    float exposure = 0.0;
    for (int i = 0; i < 4; i++) {
        float weight = gradeBandWeight(lightness, i);
        float hue = radians(wheels[i].x);
        float saturation = setting(wheels[i].y);
        lab.yz += vec2(cos(hue), sin(hue)) * saturation * ${f(COLOR_GRADE_AB_SCALE)} * weight;
        exposure += setting(wheels[i].z) * ${f(COLOR_GRADE_LUMINANCE_EV)} * weight;
    }
    vec3 linear = gamutClipDesat(oklabToLinear(lab), lab) * exp2(exposure);
    vec3 finalLab = linearToOklab(linear);
    return linearToSrgb(gamutClipDesat(linear, finalLab));
}
vec3 applyCalibration(vec3 rgb) {
    rgb = max(u_calibrationMatrix * rgb, vec3(0.0));
    float shadowWeight = 1.0 - smoothstep(${f(CALIBRATION_SHADOW_START)}, ${f(CALIBRATION_SHADOW_END)}, dot(rgb, LUMW));
    vec3 shadowMultiplier = vec3(1.0) + u_calibrationShadowTint * ${f(CALIBRATION_SHADOW_TINT_SCALE)}
        * shadowWeight * vec3(1.0, -2.0, 1.0);
    return max(rgb * shadowMultiplier, vec3(0.0));
}
vec3 applyScene(vec2 uv, out vec2 imageUv) {
    imageUv = lensUv(uv);
    if (any(lessThan(imageUv, vec2(0.0))) || any(greaterThan(imageUv, vec2(1.0)))) return vec3(0.0);
    vec3 rgb = texture(u_source, imageUv).rgb * lensVignetteGain(imageUv);
    if (u_applyWb) rgb = max(u_wbMatrix * rgb, vec3(0.0));
    rgb = applyCalibration(rgb);
    if (u_dngActive) {
        rgb = u_dngSrgbToProPhoto * rgb * exp2(u_dngBaselineExposure);
        if (!u_filmActive) {
            if (u_dngHueActive) rgb = dngApplyTable(rgb, u_dngHueSat, u_dngHueDims, u_dngHueEncoding);
            if (u_dngLookActive) rgb = dngApplyTable(rgb, u_dngLook, u_dngLookDims, u_dngLookEncoding);
        }
    }
    rgb *= exp2(u_exposure);
    float Y = dot(rgb, LUMW);
    float ev = log2(max(Y, 1e-6));
    float wh = gaussEv(ev, ${f(TONE_EV_HIGHLIGHTS_CENTER)});
    float ws = gaussEv(ev, ${f(TONE_EV_SHADOWS_CENTER)});
    float ww = gaussEv(ev, ${f(TONE_EV_WHITES_CENTER)});
    float wb = gaussEv(ev, ${f(TONE_EV_BLACKS_CENTER)});
    float hl = setting(u_regions.x);
    float hlScale = hl < 0.0 ? 1.0 : ${f(TONE_HIGHLIGHTS_POS_SCALE)};
    float deltaEv = ${f(TONE_HIGHLIGHTS_FACTOR)} * hl * hlScale * wh
        + ${f(TONE_SHADOWS_FACTOR)} * setting(u_regions.y) * ws
        + ${f(TONE_WHITES_FACTOR)} * setting(u_regions.z) * ww
        + ${f(TONE_BLACKS_FACTOR)} * setting(u_regions.w) * wb;
    rgb *= exp2(deltaEv);
    float Y2 = dot(rgb, LUMW);
    float t = pow(clamp(Y2, 0.0, 1.0), 1.0 / 2.2);
    float t3 = .5 + (t - .5) * (1.0 + ${f(CONTRAST_FACTOR)} * setting(u_contrast));
    t3 = t3 < 0.0 ? 0.0 : (t3 > 1.0 ? 1.0 + (t3 - 1.0) / (1.0 + 4.0 * (t3 - 1.0)) : t3);
    rgb *= pow(max(t3, 0.0), 2.2) / max(Y2, 1e-6);
    float d = setting(u_dehaze);
    if (abs(d) > 1e-5) rgb = max((rgb - vec3(${f(DEHAZE_AIRLIGHT_FACTOR)} * d)) / (1.0 - ${f(DEHAZE_AIRLIGHT_FACTOR)} * d), vec3(0.0));
    return rgb;
}
vec3 applyColor(vec2 uv, out vec2 imageUv) {
    vec3 rgb = applyScene(uv, imageUv);
    if (any(lessThan(imageUv, vec2(0.0))) || any(greaterThan(imageUv, vec2(1.0)))) return vec3(0.0);
    float d = setting(u_dehaze);
    vec3 c = linearToSrgb(clamp(u_dngActive ? u_dngProPhotoToSrgb * rgb : rgb, 0.0, 1.0));
    if (u_filmActive) {
        float glow = texture(u_filmGlow, uv).r;
        vec3 film = filmTransform(rgb, glow, uv * u_sourceSize, min(u_sourceSize.x, u_sourceSize.y));
        c = mix(c, film, clamp(u_filmStrength, 0.0, 1.0));
    } else {
        if (u_dngActive) {
            c = linearToSrgb(clamp(u_dngProPhotoToSrgb * dngApplyTone(rgb), 0.0, 1.0));
        } else {
            c = vec3(texture(u_baseCurve, vec2(c.r, .5)).r, texture(u_baseCurve, vec2(c.g, .5)).r, texture(u_baseCurve, vec2(c.b, .5)).r);
            c = scaleOklabChroma(c, u_baseProfileSat, 0.0, 0.0, 0.0);
        }
        vec3 mainCurve = vec3(texture(u_curve, vec2(c.r, .5)).r, texture(u_curve, vec2(c.g, .5)).r, texture(u_curve, vec2(c.b, .5)).r);
        c = vec3(texture(u_curve, vec2(mainCurve.r, .5)).g, texture(u_curve, vec2(mainCurve.g, .5)).b, texture(u_curve, vec2(mainCurve.b, .5)).a);
    }
    if (u_useHsl) {
        vec3 beforeHsl = c;
        vec3 hsv = rgbToHsv(c);
        float hue = hsv.x * 360.0;
        float neutral = smoothstep(.04, .18, hsv.y);
        float hueDelta = 0.0;
        float satDelta = 0.0;
        float lumDelta = 0.0;
        float grayDelta = 0.0;
        for (int i = 0; i < 8; i++) {
            float weight = bandWeight(hue, i);
            hueDelta += weight * neutral * setting(u_hue[i]) * ${f(HUE_SHIFT_DEGREES)};
            satDelta += weight * neutral * setting(u_hslSat[i]);
            lumDelta += weight * neutral * setting(u_hslLum[i]) * ${f(HSL_LUMINANCE_FACTOR)};
            grayDelta += weight * setting(u_gray[i]) * ${f(GRAY_MIXER_FACTOR)};
        }
        if (u_grayscale) {
            c = vec3(dot(beforeHsl, LUMW) * (1.0 + grayDelta));
        } else {
            hsv.x = fract((hue + hueDelta) / 360.0);
            hsv.y *= 1.0 + satDelta;
            hsv.z *= 1.0 + lumDelta;
            hsv.y = sat(hsv.y);
            c = scaleOklabChroma(hsvToRgb(hsv), 1.0, setting(u_saturation), setting(u_vibrance), d);
        }
    }
    if (!u_grayscale) c = applyCameraProfileAb(c);
    c = applyGrade(c);
    return c;
}
`;

const LUMA_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
${COLOR_UNIFORMS}
${COLOR_MATH}
${DNG_GLSL}
${FILM_SHADER}
${COLOR_FUNCTION}
void main() {
    vec2 imageUv;
    vec3 c = applyColor(v_uv, imageUv);
    outColor = vec4(vec3(dot(c, LUMW)), 1.0);
}`;

const FILM_LUMA_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
${COLOR_UNIFORMS}
${COLOR_MATH}
${DNG_GLSL}
${FILM_SHADER}
${COLOR_FUNCTION}
void main() {
    float excess = 0.0;
    vec2 stepUv = 1.0 / u_sourceSize;
    // The glow field is 1/8 scale. Max-pool a sparse 8x8 footprint so a
    // one-pixel lamp or specular is not lost before the wide optical blur.
    for (int y = -3; y <= 3; y += 2) {
        for (int x = -3; x <= 3; x += 2) {
            vec2 imageUv;
            vec3 scene = applyScene(clamp(v_uv + vec2(float(x), float(y)) * stepUv, 0.0, 1.0), imageUv);
            excess = max(excess, max(dot(scene, LUMW) - u_filmHalation.y, 0.0));
        }
    }
    outColor = vec4(vec3(excess), 1.0);
}`;

const BLUR_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_image;
uniform vec2 u_direction;
uniform float u_sigma;
void main() {
    float sigma = max(u_sigma, .35);
    vec4 sum = texture(u_image, v_uv);
    float total = 1.0;
    for (int i = 1; i <= 64; i++) {
        float x = float(i);
        float weight = exp(-.5 * x * x / (sigma * sigma));
        if (x > sigma * 3.0) weight = 0.0;
        sum += (texture(u_image, v_uv + u_direction * x) + texture(u_image, v_uv - u_direction * x)) * weight;
        total += 2.0 * weight;
    }
    outColor = sum / max(total, 1e-6);
}`;

// Display-referred Develop and Library previews bypass the linear RAW pipeline.
// This tiny present pass keeps the first image inside the same canvas that the
// full-quality renderer will take over, so the swap cannot move the workspace.
const DISPLAY_PREVIEW_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_preview;
void main() { outColor = texture(u_preview, v_uv); }`;

const PRE_DETAIL_FUNCTIONS = `
bool hueWindow(float hue, float lo, float hi) {
    return lo <= hi ? hue >= lo && hue <= hi : hue >= lo || hue <= hi;
}
vec3 defringeAt(vec3 c, float L, vec2 uv) {
    if (u_defringePurple.x <= 0.0 && u_defringeGreen.x <= 0.0) return c;
    float sharp = texture(u_blurSharp, uv).r;
    float edge = smoothstep(${f(DEFRINGE_EDGE_LOW)}, ${f(DEFRINGE_EDGE_HIGH)}, abs(L - sharp));
    vec3 hsv = rgbToHsv(c);
    float hue = hsv.x * 360.0;
    float amount = 0.0;
    if (u_defringePurple.x > 0.0 && hueWindow(hue, u_defringePurple.y * ${f(DEFRINGE_HUE_SCALE)}, u_defringePurple.z * ${f(DEFRINGE_HUE_SCALE)})) amount = u_defringePurple.x;
    if (u_defringeGreen.x > 0.0 && hueWindow(hue, u_defringeGreen.y * ${f(DEFRINGE_HUE_SCALE)}, u_defringeGreen.z * ${f(DEFRINGE_HUE_SCALE)})) amount = max(amount, u_defringeGreen.x);
    hsv.y *= 1.0 - amount * edge;
    return hsvToRgb(hsv);
}
vec3 luminanceNoiseReduceAt(vec3 c, float L, vec2 uv) {
    if (u_luminanceSmoothing <= 0.0) return c;
    float center = texture(u_lumaNr, uv).r;
    vec2 dx = vec2(1.0 / max(u_sourceSize.x * .5, 1.0), 0.0);
    vec2 dy = vec2(0.0, 1.0 / max(u_sourceSize.y * .5, 1.0));
    float total = ${f(NR_LUMA_SPATIAL_CENTER)};
    float filtered = center * ${f(NR_LUMA_SPATIAL_CENTER)};
    for (int i = 0; i < 4; i++) {
        vec2 offset = i == 0 ? dx : i == 1 ? -dx : i == 2 ? dy : -dy;
        float neighbor = texture(u_lumaNr, uv + offset).r;
        float weight = ${f(NR_LUMA_SPATIAL_AXIS)} * exp(-pow(neighbor - center, 2.0) / (2.0 * ${f(NR_LUMA_SIGMA)} * ${f(NR_LUMA_SIGMA)}));
        filtered += neighbor * weight;
        total += weight;
    }
    float sharp = texture(u_blurSharp, uv).r;
    float edge = smoothstep(${f(NR_DETAIL_EDGE_LOW)}, ${f(NR_DETAIL_EDGE_HIGH)}, abs(L - sharp));
    float amount = setting(u_luminanceSmoothing) * (1.0 - edge * setting(u_luminanceDetail));
    float residual = filtered / total - L;
    c += vec3(residual * amount);
    c += vec3(-residual * amount * setting(u_luminanceContrast) * ${f(NR_CONTRAST_RESIDUAL)});
    return clamp(c, 0.0, 1.0);
}`;

const PRE_DETAIL_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
${COLOR_UNIFORMS}
uniform sampler2D u_blurSharp;
uniform sampler2D u_lumaNr;
uniform float u_luminanceSmoothing;
uniform float u_luminanceDetail;
uniform float u_luminanceContrast;
uniform vec4 u_defringePurple;
uniform vec4 u_defringeGreen;
${COLOR_MATH}
${DNG_GLSL}
${FILM_SHADER}
${COLOR_FUNCTION}
${PRE_DETAIL_FUNCTIONS}
void main() {
    vec2 imageUv;
    vec3 c = applyColor(v_uv, imageUv);
    float L = dot(c, LUMW);
    c = defringeAt(c, L, v_uv);
    c = luminanceNoiseReduceAt(c, L, v_uv);
    outColor = vec4(c, 1.0);
}`;

const MAIN_FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
in vec2 v_uv;
out vec4 outColor;
${COLOR_UNIFORMS}
uniform sampler2D u_blurLarge;
uniform sampler2D u_blurSmall;
uniform sampler2D u_blurSharp;
uniform bool u_useLarge;
uniform bool u_useSmall;
uniform bool u_useSharp;
uniform float u_clarity;
uniform float u_texture;
uniform float u_sharpness;
uniform float u_vignette;
uniform vec3 u_vignetteShape;
uniform float u_grain;
uniform float u_grainSize;
uniform float u_grainFrequency;
uniform uint u_seed;
uniform sampler2D u_lumaNr;
uniform float u_luminanceSmoothing;
uniform float u_colorNoiseReduction;
uniform float u_luminanceDetail;
uniform float u_luminanceContrast;
uniform float u_sharpenMasking;
uniform sampler2D u_preDetail;
uniform bool u_preDetailActive;
uniform int u_softProofProfile;
uniform bool u_gamutWarning;
uniform mat3 u_srgbToXyz;
uniform mat3 u_xyzToSrgb;
uniform mat3 u_proofToXyz;
uniform mat3 u_xyzToProof;
uniform vec4 u_defringePurple;
uniform vec4 u_defringeGreen;
uniform sampler2D u_maskAtlas;
uniform int u_localActive[${LOCAL_RENDER_CAP}];
uniform float u_localAmount[${LOCAL_RENDER_CAP}];
uniform vec4 u_localLightA[${LOCAL_RENDER_CAP}];
uniform vec4 u_localLightB[${LOCAL_RENDER_CAP}];
uniform vec4 u_localEffects[${LOCAL_RENDER_CAP}];
uniform vec4 u_localColor[${LOCAL_RENDER_CAP}];
uniform int u_maskOverlay;
${COLOR_MATH}
${DNG_GLSL}
${FILM_SHADER}
${COLOR_FUNCTION}
float localMask(int index, vec2 imageUv) {
    int column = index % ${LOCAL_MASK_ATLAS_COLUMNS};
    int row = index / ${LOCAL_MASK_ATLAS_COLUMNS};
    // Settings/canvas mask coordinates have a top-left origin. The source
    // texture's typed upload has a GL bottom-left origin, so only mask Y flips.
    vec2 maskUv = vec2(imageUv.x, 1.0 - imageUv.y);
    vec2 atlasUv = (vec2(float(column), float(row)) + clamp(maskUv, 0.0, 1.0)) / float(${LOCAL_MASK_ATLAS_COLUMNS});
    return texture(u_maskAtlas, atlasUv).r;
}
vec3 applyLocalCorrection(vec3 rgbLinear, float baseLuma, vec2 blurs, vec4 lightA, vec4 lightB, vec4 effects, vec4 color, float m) {
    vec3 rgb = rgbLinear * exp2(lightA.x * m);
    float mired = effects.y * m;
    float tint = effects.z * m;
    rgb.r *= exp2(mired * ${f(LOCAL_WB_TEMP_FACTOR)});
    rgb.b *= exp2(-mired * ${f(LOCAL_WB_TEMP_FACTOR)});
    rgb.g *= exp2(-tint * ${f(LOCAL_WB_TINT_FACTOR)});
    float Y = dot(rgb, LUMW);
    float ev = log2(max(Y, 1e-6));
    float hlScale = lightA.z < 0.0 ? 1.0 : ${f(TONE_HIGHLIGHTS_POS_SCALE)};
    float deltaEv = m * (
        ${f(TONE_HIGHLIGHTS_FACTOR)} * lightA.z * hlScale * gaussEv(ev, ${f(TONE_EV_HIGHLIGHTS_CENTER)})
        + ${f(TONE_SHADOWS_FACTOR)} * lightA.w * gaussEv(ev, ${f(TONE_EV_SHADOWS_CENTER)})
        + ${f(TONE_WHITES_FACTOR)} * lightB.x * gaussEv(ev, ${f(TONE_EV_WHITES_CENTER)})
        + ${f(TONE_BLACKS_FACTOR)} * lightB.y * gaussEv(ev, ${f(TONE_EV_BLACKS_CENTER)}));
    rgb *= exp2(deltaEv);
    float Y2 = dot(rgb, LUMW);
    float tone = pow(clamp(Y2, 0.0, 1.0), 1.0 / 2.2);
    tone = .5 + (tone - .5) * (1.0 + ${f(CONTRAST_FACTOR)} * lightA.y * m);
    tone = tone < 0.0 ? 0.0 : (tone > 1.0 ? 1.0 + (tone - 1.0) / (1.0 + 4.0 * (tone - 1.0)) : tone);
    rgb *= pow(max(tone, 0.0), 2.2) / max(Y2, 1e-6);
    float dehaze = lightB.w * m;
    rgb = max((rgb - vec3(${f(DEHAZE_AIRLIGHT_FACTOR)} * dehaze)) / (1.0 - ${f(DEHAZE_AIRLIGHT_FACTOR)} * dehaze), vec3(0.0));
    vec3 c = linearToSrgb(clamp(rgb, 0.0, 1.0));
    vec3 lab = linearToOklab(srgbToLinear(c));
    float angle = radians(color.x) * m;
    mat2 rotateHue = mat2(cos(angle), sin(angle), -sin(angle), cos(angle));
    lab.yz = rotateHue * lab.yz * max(1.0 + effects.w * m, 0.0);
    c = linearToSrgb(clamp(oklabToLinear(lab), 0.0, 1.0));
    float midtones = clamp(4.0 * baseLuma * (1.0 - baseLuma), 0.0, 1.0);
    float largeResidual = clamp(baseLuma - blurs.x, -${f(CLARITY_RESIDUAL_MAX)}, ${f(CLARITY_RESIDUAL_MAX)});
    float smallResidual = clamp(baseLuma - blurs.y, -${f(CLARITY_RESIDUAL_MAX)}, ${f(CLARITY_RESIDUAL_MAX)});
    c += vec3(largeResidual * ${f(CLARITY_FACTOR)} * lightB.z * midtones * m);
    c += vec3(smallResidual * ${f(TEXTURE_FACTOR)} * effects.x * m);
    return clamp(c, 0.0, 1.0);
}
float noiseHash(ivec2 pixel) {
    uint h = (uint(pixel.x) * ${GRAIN_X_MULTIPLIER}u + uint(pixel.y) * ${GRAIN_Y_MULTIPLIER}u) ^ u_seed;
    h = (h ^ (h >> ${GRAIN_HASH_SHIFT}u)) * ${GRAIN_HASH_MULTIPLIER}u;
    return float((h >> ${GRAIN_OUTPUT_SHIFT}u) & ${GRAIN_OUTPUT_MASK}u) / 65535.0;
}
${PRE_DETAIL_FUNCTIONS}
int reflectedIndex(int value, int size) {
    if (size <= 1) return 0;
    if (value < 0) return min(-value, size - 1);
    if (value >= size) return max(2 * size - value - 2, 0);
    return value;
}
vec3 colorNoiseReduce(vec3 c, vec2 uv) {
    if (u_colorNoiseReduction <= 0.0) return c;
    ivec2 size = textureSize(u_preDetail, 0);
    ivec2 pixel = clamp(ivec2(floor(uv * vec2(size))), ivec2(0), size - 1);
    vec3 lab = linearToOklab(srgbToLinear(clamp(c, 0.0, 1.0)));
    vec2 averageAb = lab.yz;
    averageAb += linearToOklab(srgbToLinear(texelFetch(u_preDetail, ivec2(reflectedIndex(pixel.x - 1, size.x), pixel.y), 0).rgb)).yz;
    averageAb += linearToOklab(srgbToLinear(texelFetch(u_preDetail, ivec2(reflectedIndex(pixel.x + 1, size.x), pixel.y), 0).rgb)).yz;
    averageAb += linearToOklab(srgbToLinear(texelFetch(u_preDetail, ivec2(pixel.x, reflectedIndex(pixel.y - 1, size.y)), 0).rgb)).yz;
    averageAb += linearToOklab(srgbToLinear(texelFetch(u_preDetail, ivec2(pixel.x, reflectedIndex(pixel.y + 1, size.y)), 0).rgb)).yz;
    lab.yz = mix(lab.yz, averageAb / 5.0, setting(u_colorNoiseReduction));
    return clamp(linearToSrgb(gamutClipDesat(oklabToLinear(lab), lab)), 0.0, 1.0);
}
void main() {
    vec2 imageUv;
    vec3 c;
    if (u_preDetailActive) {
        c = texture(u_preDetail, v_uv).rgb;
        imageUv = v_uv;
    } else {
        c = applyColor(v_uv, imageUv);
        float sourceL = dot(c, LUMW);
        c = defringeAt(c, sourceL, v_uv);
        c = luminanceNoiseReduceAt(c, sourceL, v_uv);
    }
    c = colorNoiseReduce(c, v_uv);
    float L = dot(c, LUMW);
    vec2 localBlurs = vec2(texture(u_blurLarge, v_uv).r, texture(u_blurSmall, v_uv).r);
    for (int i = 0; i < ${LOCAL_RENDER_CAP}; i++) {
        if (u_localActive[i] == 0) continue;
        float m = clamp(localMask(i, v_uv) * u_localAmount[i], 0.0, 2.0);
        if (m > 1e-6) c = applyLocalCorrection(srgbToLinear(c), L, localBlurs, u_localLightA[i], u_localLightB[i], u_localEffects[i], u_localColor[i], m);
    }
    if (u_useLarge) {
        float wm = clamp(4.0 * L * (1.0 - L), 0.0, 1.0);
        float residual = clamp(L - texture(u_blurLarge, v_uv).r, -${f(CLARITY_RESIDUAL_MAX)}, ${f(CLARITY_RESIDUAL_MAX)});
        c += vec3(residual * ${f(CLARITY_FACTOR)} * setting(u_clarity) * wm);
    }
    if (u_useSmall) {
        float residual = clamp(L - texture(u_blurSmall, v_uv).r, -${f(CLARITY_RESIDUAL_MAX)}, ${f(CLARITY_RESIDUAL_MAX)});
        c += vec3(residual * ${f(TEXTURE_FACTOR)} * setting(u_texture));
    }
    if (u_useSharp) {
        float r = L - texture(u_blurSharp, v_uv).r;
        float mag = abs(r);
        float gated = max(mag - ${f(SHARPEN_THRESHOLD)}, 0.0) / max(1.0 - ${f(SHARPEN_THRESHOLD)}, 1e-6);
        float edgeMask = smoothstep(${f(SHARPEN_MASK_EDGE_LOW)}, ${f(SHARPEN_MASK_EDGE_HIGH)}, mag);
        gated *= mix(1.0, edgeMask, setting(u_sharpenMasking));
        c += vec3(sign(r) * gated * (u_sharpness / 150.0) * ${f(SHARPEN_FACTOR)});
    }
    float a = setting(u_vignette);
    if (abs(a) > 1e-5) {
        vec2 center = (u_crop.xy + u_crop.zw) * .5;
        float cropAspect = (u_crop.z - u_crop.x) * u_sourceSize.x / max((u_crop.w - u_crop.y) * u_sourceSize.y, 1.0);
        float roundness = 1.0 + setting(u_vignetteShape.z) * ${f(VIGNETTE_ROUNDNESS_FACTOR)};
        vec2 p = v_uv - center;
        p.x *= 2.0 * cropAspect / max(roundness, 1e-6);
        p.y *= 2.0 * max(roundness, 1e-6);
        float rho = length(p);
        float mid = ${f(VIGNETTE_MIDPOINT_MIN)} + clamp(u_vignetteShape.x / 100.0, 0.0, 1.0) * ${f(VIGNETTE_MIDPOINT_RANGE)};
        float feather = ${f(VIGNETTE_FEATHER_MIN)} + clamp(u_vignetteShape.y / 100.0, 0.0, 1.0) * ${f(VIGNETTE_FEATHER_RANGE)};
        float amount = a * smoothstep(mid, mid + feather, rho) * ${f(VIGNETTE_FACTOR)};
        c = amount < 0.0 ? c * (1.0 + amount) : c + (1.0 - c) * amount;
    }
    if (u_grain > 0.0) {
        float cell = ${f(GRAIN_CELL_SIZE_MIN)} + clamp(u_grainSize / 100.0, 0.0, 1.0) * ${f(GRAIN_CELL_SIZE_RANGE)};
        float frequency = ${f(GRAIN_FREQUENCY_MIN)} + clamp(u_grainFrequency / 100.0, 0.0, 1.0) * ${f(GRAIN_FREQUENCY_RANGE)};
        vec2 topDownUv = vec2(v_uv.x, 1.0 - v_uv.y);
        ivec2 pixel = ivec2(floor((topDownUv * u_sourceSize - .5) / cell * frequency));
        c += vec3((noiseHash(pixel) - .5) * setting(u_grain) * ${f(GRAIN_FACTOR)});
    }
    if (u_maskOverlay >= 0) {
        float overlay = clamp(localMask(u_maskOverlay, imageUv), 0.0, 1.0) * .5;
        c = mix(c, vec3(1.0, 0.0, 0.0), overlay);
    }
    vec3 proof = u_xyzToProof * (u_srgbToXyz * srgbToLinear(clamp(c, 0.0, 1.0)));
    bool outsideProof = any(lessThan(proof, vec3(0.0))) || any(greaterThan(proof, vec3(1.0)));
    if (u_softProofProfile > 0) c = linearToSrgb(u_xyzToSrgb * (u_proofToXyz * clamp(proof, 0.0, 1.0)));
    if (u_softProofProfile == 4) c = vec3(${f(SOFT_PROOF_PAPER_BLACK)}) + c * ${f(SOFT_PROOF_PAPER_WHITE - SOFT_PROOF_PAPER_BLACK)};
    if (u_gamutWarning && outsideProof) c = mix(c, vec3(1.0, .08, .48), .42);
    outColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;

const RETOUCH_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_retouchSource;
uniform int u_retouchActive[${RETOUCH_RENDER_CAP}];
uniform vec4 u_retouchSourceData[${RETOUCH_RENDER_CAP}];
uniform vec4 u_retouchDestinationData[${RETOUCH_RENDER_CAP}];
uniform bool u_healOverlay;

vec3 ringMean(vec2 center, float radius) {
    vec3 total = vec3(0.0);
    for (int tap = 0; tap < ${RETOUCH_RING_TAPS}; tap++) {
        float angle = float(tap) * 6.283185307179586 / float(${RETOUCH_RING_TAPS});
        vec2 offset = vec2(cos(angle), sin(angle)) * radius * ${f(RETOUCH_RING_SCALE)};
        total += texture(u_retouchSource, clamp(center + offset, 0.0, 1.0)).rgb;
    }
    return total / float(${RETOUCH_RING_TAPS});
}

void main() {
    vec3 result = texture(u_retouchSource, v_uv).rgb;
    for (int index = 0; index < ${RETOUCH_RENDER_CAP}; index++) {
        if (u_retouchActive[index] == 0) continue;
        vec4 sourceData = u_retouchSourceData[index];
        vec4 destinationData = u_retouchDestinationData[index];
        float radius = max(sourceData.z, ${f(RETOUCH_MIN_RADIUS)});
        float distance = length(v_uv - destinationData.xy);
        float hardEdge = radius * (1.0 - sourceData.w);
        float weight = (1.0 - smoothstep(hardEdge, radius, distance)) * destinationData.z;
        vec3 sampled = texture(u_retouchSource, clamp(v_uv + sourceData.xy - destinationData.xy, 0.0, 1.0)).rgb;
        if (destinationData.w > 0.5) {
            sampled += ringMean(destinationData.xy, radius) - ringMean(sourceData.xy, radius);
        }
        result = mix(result, sampled, weight);
        if (u_healOverlay) {
            float line = 1.0 - smoothstep(0.0, max(radius * .04, .001), abs(distance - radius));
            result = mix(result, vec3(1.0, .65, .15), line * .8);
        }
    }
    outColor = vec4(clamp(result, 0.0, 1.0), 1.0);
}`;

function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const message = gl.getShaderInfoLog(shader);
        const line = Number(message?.match(/ERROR:\s*\d+:(\d+)/)?.[1] || 0);
        const context = line ? source.split('\n').slice(Math.max(0, line - 3), line + 2)
            .map((value, index) => `${Math.max(1, line - 2) + index}: ${value}`).join('\n') : '';
        gl.deleteShader(shader);
        throw new Error(`Develop shader compile failed: ${message}${context ? `\n${context}` : ''}`);
    }
    return shader;
}

function program(gl, fragment) {
    const result = gl.createProgram();
    gl.attachShader(result, compile(gl, gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(result, compile(gl, gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(result);
    if (!gl.getProgramParameter(result, gl.LINK_STATUS)) throw new Error(`Develop shader link failed: ${gl.getProgramInfoLog(result)}`);
    return result;
}

function texture(gl, width, height, {
    data = null, filter = gl.LINEAR, internalFormat = gl.RGBA32F, type = gl.FLOAT,
} = {}) {
    const result = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, result);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, width, height, 0, gl.RGBA, type, data);
    return result;
}

function float32ToHalf(values) {
    const result = new Uint16Array(values.length);
    const float = new Float32Array(1);
    const bits = new Uint32Array(float.buffer);
    for (let index = 0; index < values.length; index += 1) {
        float[0] = values[index];
        const value = bits[0];
        const sign = (value >>> 16) & 0x8000;
        let exponent = ((value >>> 23) & 0xff) - 127 + 15;
        let mantissa = value & 0x7fffff;
        if (exponent <= 0) {
            if (exponent < -10) result[index] = sign;
            else {
                mantissa = (mantissa | 0x800000) >>> (1 - exponent);
                result[index] = sign | ((mantissa + 0x1000) >>> 13);
            }
        } else if (exponent >= 31) {
            result[index] = sign | 0x7c00 | (mantissa ? 0x0200 : 0);
        } else {
            if (mantissa & 0x1000) {
                mantissa += 0x2000;
                if (mantissa & 0x800000) { mantissa = 0; exponent += 1; }
            }
            result[index] = sign | (Math.min(31, exponent) << 10) | (mantissa >>> 13);
        }
    }
    return result;
}

function target(gl, width, height) {
    const color = texture(gl, width, height, { internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT });
    const framebuffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, color, 0);
    const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    if (status !== gl.FRAMEBUFFER_COMPLETE) {
        const error = gl.getError();
        throw new Error(`Develop float framebuffer unavailable (${width}×${height}, 0x${status.toString(16)}, gl 0x${error.toString(16)})`);
    }
    return { framebuffer, texture: color, width, height };
}

function bindUnit(gl, value, unit) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, value);
}

function maskTexture(gl, width, height, data = null) {
    const result = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, result);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, width, height, 0, gl.RED, gl.UNSIGNED_BYTE, data);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    return result;
}

function maskSourceSize(source) {
    return [Number(source?.width || source?.naturalWidth || 1), Number(source?.height || source?.naturalHeight || 1)];
}

function drawMaskSource(context, source, x, y, width, height) {
    if (!source) return;
    if (source.data && Number(source.width) > 0 && Number(source.height) > 0) {
        const scratch = document.createElement('canvas');
        scratch.width = Number(source.width); scratch.height = Number(source.height);
        const scratchContext = scratch.getContext('2d');
        const imageData = typeof ImageData !== 'undefined' && source instanceof ImageData
            ? source : new ImageData(new Uint8ClampedArray(source.data), source.width, source.height);
        scratchContext.putImageData(imageData, 0, 0);
        context.drawImage(scratch, x, y, width, height);
    } else {
        context.drawImage(source, x, y, width, height);
    }
}

export class DevelopRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.gl = canvas.getContext('webgl2', { alpha: false, antialias: false, preserveDrawingBuffer: true });
        if (!this.gl) throw new Error('WebGL2 is required for Develop');
        if (!this.gl.getExtension('EXT_color_buffer_float')) throw new Error('Float render targets are unavailable');
        this.mainProgram = program(this.gl, MAIN_FRAGMENT);
        this.preDetailProgram = program(this.gl, PRE_DETAIL_FRAGMENT);
        this.geometryProgram = program(this.gl, GEOMETRY_FRAGMENT);
        this.lumaProgram = program(this.gl, LUMA_FRAGMENT);
        this.filmLumaProgram = program(this.gl, FILM_LUMA_FRAGMENT);
        this.blurProgram = program(this.gl, BLUR_FRAGMENT);
        this.retouchProgram = program(this.gl, RETOUCH_FRAGMENT);
        this.displayPreviewProgram = program(this.gl, DISPLAY_PREVIEW_FRAGMENT);
        this.settings = {};
        this.meta = {};
        this.geometryEnabled = true;
        this.view = { scale: 1, center: { u: .5, v: .5 } };
        this.width = 1;
        this.height = 1;
        this.dirty = false;
        this.ready = false;
        this.displayPreview = false;
        this.previewSource = null;
        this.frame = 0;
        this.createGeometry();
        this.source = texture(this.gl, 1, 1, {
            data: float32ToHalf(new Float32Array([0, 0, 0, 1])),
            internalFormat: this.gl.RGBA16F, type: this.gl.HALF_FLOAT,
        });
        this.canvas.width = 1;
        this.canvas.height = 1;
        this.baseCurve = null;
        this.dngHueSat = null;
        this.dngLook = null;
        this.dngTone = null;
        this.dngProfileKey = '';
        this.dngHueDims = [0, 0, 0];
        this.dngLookDims = [0, 0, 0];
        this.filmHd = null;
        this.filmPrint = null;
        this.filmTables = null;
        this.filmSlug = '';
        this.filmRequestSlug = '';
        this.filmReadyPromise = Promise.resolve();
        this.maskAtlas = maskTexture(this.gl, LOCAL_MASK_ATLAS_COLUMNS, LOCAL_MASK_ATLAS_COLUMNS, new Uint8Array(LOCAL_MASK_ATLAS_COLUMNS ** 2));
        this.maskRasters = [];
        this.maskRasterSources = [];
        this.maskOverlay = -1;
        this.healOverlay = false;
        this.softProof = { profile: 'off', warning: false };
        this.baseProfileKey = '';
        this.updateBaseCurve(null);
        this.updateCurve({});
        this.updateDngTextures(null, 5500);
        this.updateFilmTextures(null);
    }

    createGeometry() {
        const gl = this.gl;
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
        for (const p of [this.mainProgram, this.preDetailProgram, this.geometryProgram, this.lumaProgram, this.filmLumaProgram, this.blurProgram, this.retouchProgram, this.displayPreviewProgram]) {
            const location = gl.getAttribLocation(p, 'a_position');
            gl.enableVertexAttribArray(location);
            gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
        }
        this.vao = vao;
    }

    uploadSource(data, width, height) {
        const gl = this.gl;
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        this.displayPreview = false;
        if (this.previewSource) {
            gl.deleteTexture(this.previewSource);
            this.previewSource = null;
        }
        if (this.source) gl.deleteTexture(this.source);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        this.source = texture(gl, width, height, {
            data: float32ToHalf(data), internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        this.width = width;
        this.height = height;
        this.canvas.width = width;
        this.canvas.height = height;
        this.rebuildTargets();
        this.ready = true;
        this.requestRender();
    }

    uploadDisplayPreview(image) {
        // Paint an 8-bit display-referred preview without applying RAW settings.
        const gl = this.gl;
        const width = Number(image?.width);
        const height = Number(image?.height);
        if (!(width > 0 && height > 0)) throw new Error('Develop preview has invalid dimensions.');
        if (this.previewSource) gl.deleteTexture(this.previewSource);
        this.previewSource = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, this.previewSource);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        this.displayPreview = true;
        this.width = width;
        this.height = height;
        this.canvas.width = width;
        this.canvas.height = height;
        this.ready = true;
        this.requestRender();
    }

    rebuildTargets() {
        const gl = this.gl;
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        const width = Math.max(1, Math.ceil(this.width / 2));
        const height = Math.max(1, Math.ceil(this.height / 2));
        const next = [target(gl, width, height), target(gl, width, height), target(gl, width, height), target(gl, width, height), target(gl, width, height)];
        const filmWidth = Math.max(1, Math.ceil(this.width / 8));
        const filmHeight = Math.max(1, Math.ceil(this.height / 8));
        const filmNext = [target(gl, filmWidth, filmHeight), target(gl, filmWidth, filmHeight), target(gl, filmWidth, filmHeight)];
        for (const item of this.targets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        for (const item of this.filmTargets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        for (const item of [this.retouchTarget, this.preDetailTarget, this.processedTarget]) if (item) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        [this.lumaTarget, this.blurTemp, this.blurLarge, this.blurSmall, this.blurSharp] = next;
        this.targets = next;
        [this.filmLumaTarget, this.filmBlurTemp, this.filmGlow] = filmNext;
        this.filmTargets = filmNext;
        this.retouchTarget = target(gl, this.width, this.height);
        this.preDetailTarget = target(gl, this.width, this.height);
        this.processedTarget = target(gl, this.width, this.height);
    }

    updateBaseCurve(profile = null, settings = {}, baseKind = 'raw') {
        const gl = this.gl;
        if (this.baseCurve) gl.deleteTexture(this.baseCurve);
        const lut = buildBaseProfileLut(profile, settings, baseKind);
        const data = new Float32Array(256 * 4);
        for (let i = 0; i < 256; i += 1) {
            data[i * 4] = lut[i];
            data[i * 4 + 1] = lut[i];
            data[i * 4 + 2] = lut[i];
            data[i * 4 + 3] = 1;
        }
        this.baseCurve = texture(gl, 256, 1, {
            data: float32ToHalf(data), filter: gl.LINEAR,
            internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
    }

    updateCurve(settings) {
        const key = JSON.stringify([
            settings?.ToneCurvePV2012 || null,
            settings?.ToneCurvePV2012Red || null,
            settings?.ToneCurvePV2012Green || null,
            settings?.ToneCurvePV2012Blue || null,
        ]);
        if (key === this.curveKey) return;
        this.curveKey = key;
        const gl = this.gl;
        if (this.curve) gl.deleteTexture(this.curve);
        this.curve = texture(gl, 256, 1, {
            data: float32ToHalf(buildCombinedCurveTexture(settings)), filter: gl.LINEAR,
            internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
    }

    updateDngTextures(profile, cct) {
        const key = JSON.stringify([
            profile?.source_file || profile?.library_file || '', profile?.profile_name || '',
            Number(profile?.baseline_exposure || 0), Number(cct || 5500),
        ]);
        if (key === this.dngProfileKey) return;
        this.dngProfileKey = key;
        const gl = this.gl;
        for (const value of [this.dngHueSat, this.dngLook, this.dngTone]) if (value) gl.deleteTexture(value);
        const hue = packDngTable(profile?.hue_sat_map, profile, cct);
        const look = packDngTable(profile?.look_table, profile, cct);
        const tone = packDngTone(profile) || (() => {
            const data = new Float32Array(1025 * 4);
            for (let index = 0; index < 1025; index += 1) data.set([index / 1024, index / 1024, index / 1024, 1], index * 4);
            return data;
        })();
        const identity = new Float32Array([0, 1, 1, 1, 0, 1, 1, 1]);
        this.dngHueSat = texture(gl, hue?.width || 2, hue?.height || 1, {
            data: float32ToHalf(hue?.data || identity), internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT, filter: gl.NEAREST,
        });
        this.dngLook = texture(gl, look?.width || 2, look?.height || 1, {
            data: float32ToHalf(look?.data || identity), internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT, filter: gl.NEAREST,
        });
        this.dngTone = texture(gl, 1025, 1, {
            // The ACR3 toe is steep enough that half-float tone samples amplify
            // into visible shadow error. Keep HSV tables compact RGBA16F, but
            // preserve the exact SDK curve in a sampled RGBA32F texture.
            data: tone, internalFormat: gl.RGBA32F, type: gl.FLOAT, filter: gl.NEAREST,
        });
        this.dngHueDims = hue?.dims || [0, 0, 0];
        this.dngLookDims = look?.dims || [0, 0, 0];
    }

    updateFilmTextures(tables) {
        if (tables === this.filmTextureTables) return;
        this.filmTextureTables = tables;
        const gl = this.gl;
        if (this.filmHd) gl.deleteTexture(this.filmHd);
        if (this.filmPrint) gl.deleteTexture(this.filmPrint);
        const hd = new Float32Array(256 * 4);
        const print = new Float32Array(256 * 4);
        for (let index = 0; index < 256; index += 1) {
            hd[index * 4] = tables?.hdLut[index * 3] ?? 0;
            hd[index * 4 + 1] = tables?.hdLut[index * 3 + 1] ?? 0;
            hd[index * 4 + 2] = tables?.hdLut[index * 3 + 2] ?? 0;
            hd[index * 4 + 3] = 1;
            const value = tables?.printLut[index] ?? 0;
            print.set([value, value, value, 1], index * 4);
        }
        this.filmHd = texture(gl, 256, 1, {
            data: float32ToHalf(hd), filter: gl.LINEAR,
            internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
        this.filmPrint = texture(gl, 256, 1, {
            data: float32ToHalf(print), filter: gl.LINEAR,
            internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
    }

    updateFilmStock(slug) {
        const requested = String(slug || '').trim();
        if (requested === this.filmRequestSlug) return this.filmReadyPromise;
        this.filmRequestSlug = requested;
        this.filmTables = null;
        this.filmSlug = '';
        if (!requested) {
            this.filmReadyPromise = Promise.resolve();
            this.requestRender();
            return this.filmReadyPromise;
        }
        this.filmReadyPromise = fetchFilmStock(requested).then((stock) => {
            if (this.filmRequestSlug !== requested) return;
            this.filmTables = buildFilmTables(stock);
            this.filmSlug = requested;
            this.updateFilmTextures(this.filmTables);
            this.requestRender();
        }).catch(() => {
            if (this.filmRequestSlug === requested) this.requestRender();
        });
        return this.filmReadyPromise;
    }

    waitForFilm() {
        return this.filmReadyPromise;
    }

    filmActive() {
        return Boolean(this.filmTables && this.filmSlug === String(this.settings.pa_FilmStock || '').trim());
    }

    setSettings(settings, meta = this.meta, _opts = {}) {
        this.settings = effectiveLookSettings(settings || {});
        this.meta = meta || {};
        this.colorProfile = canvasColorProfile(this.meta);
        const baseKind = this.colorProfile.base_kind || this.meta.base_kind || 'raw';
        const adobeProfile = this.colorProfile.adobe_profile || this.colorProfile.color?.adobe_profile || null;
        const profile = baseKind === 'display' || adobeProfile ? null : (this.colorProfile.camera_profile || this.colorProfile.color?.camera_profile || null);
        const profileKey = profile?.slug || profile?.model || '';
        const look = this.settings.Look;
        const lookKey = JSON.stringify([
            look?.Amount ?? null,
            look?.Parameters?.ToneCurvePV2012 ?? null,
        ]);
        const curveBaseKind = adobeProfile ? 'display' : baseKind;
        const baseProfileKey = `${curveBaseKind}|${profileKey}|${lookKey}`;
        if (baseProfileKey !== this.baseProfileKey) {
            this.baseProfileKey = baseProfileKey;
            this.updateBaseCurve(profile, this.settings, curveBaseKind);
        }
        const cct = Number(this.meta.as_shot_temperature || this.meta.as_shot?.temperature || 5500);
        this.updateDngTextures(adobeProfile, cct);
        this.updateCurve(this.settings);
        this.updateFilmStock(this.settings.pa_FilmStock);
        this.requestRender();
    }

    setSoftProof(proof = {}) {
        this.softProof = { profile: proof.profile || 'off', warning: Boolean(proof.warning) };
        this.requestRender();
    }

    setViewTransform({ scale = 1, center = { u: .5, v: .5 } } = {}) {
        const finiteScale = Number.isFinite(Number(scale)) ? Number(scale) : 1;
        const clamp = (value) => Math.max(0, Math.min(1, Number.isFinite(Number(value)) ? Number(value) : .5));
        this.view = {
            scale: Math.max(1, finiteScale),
            center: { u: clamp(center?.u), v: clamp(center?.v) },
        };
        this.requestRender();
    }

    canvasToImage(clientX, clientY) {
        const box = this.canvas.getBoundingClientRect();
        const x = (Number(clientX) - box.left) / Math.max(1, box.width);
        const y = (Number(clientY) - box.top) / Math.max(1, box.height);
        return {
            u: this.view.center.u + (x - .5) / this.view.scale,
            v: this.view.center.v + (y - .5) / this.view.scale,
        };
    }

    imageToCanvas(u, v) {
        return {
            x: .5 + (Number(u) - this.view.center.u) * this.view.scale,
            y: .5 + (Number(v) - this.view.center.v) * this.view.scale,
        };
    }

    imageRectToStage(left, top, right, bottom) {
        const canvasBox = this.canvas.getBoundingClientRect();
        const stageBox = this.canvas.parentElement.getBoundingClientRect();
        const start = this.imageToCanvas(left, top);
        const end = this.imageToCanvas(right, bottom);
        return {
            left: canvasBox.left - stageBox.left + start.x * canvasBox.width,
            top: canvasBox.top - stageBox.top + start.y * canvasBox.height,
            width: (end.x - start.x) * canvasBox.width,
            height: (end.y - start.y) * canvasBox.height,
        };
    }

    setMaskRasters(rasters = []) {
        const entries = (Array.isArray(rasters) ? rasters : []).filter((entry) => {
            const index = Number(entry?.correctionIndex);
            return Number.isInteger(index) && index >= 0 && index < LOCAL_RENDER_CAP && entry.canvasOrImageData;
        });
        const sizes = entries.map((entry) => maskSourceSize(entry.canvasOrImageData));
        const sources = entries.map((entry) => [entry.correctionIndex, entry.canvasOrImageData]);
        if (sources.length === this.maskRasterSources.length
            && sources.every(([index, source], position) => index === this.maskRasterSources[position][0] && source === this.maskRasterSources[position][1])) return;
        this.maskRasterSources = sources;
        const tileWidth = Math.max(1, ...sizes.map(([width]) => width));
        const tileHeight = Math.max(1, ...sizes.map(([, height]) => height));
        const canvas = document.createElement('canvas');
        canvas.width = tileWidth * LOCAL_MASK_ATLAS_COLUMNS;
        canvas.height = tileHeight * LOCAL_MASK_ATLAS_COLUMNS;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        for (const entry of entries) {
            const index = Number(entry.correctionIndex);
            drawMaskSource(
                context,
                entry.canvasOrImageData,
                (index % LOCAL_MASK_ATLAS_COLUMNS) * tileWidth,
                Math.floor(index / LOCAL_MASK_ATLAS_COLUMNS) * tileHeight,
                tileWidth,
                tileHeight,
            );
        }
        const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
        const red = new Uint8Array(canvas.width * canvas.height);
        for (let i = 0; i < red.length; i += 1) red[i] = rgba[i * 4];
        if (this.maskAtlas) this.gl.deleteTexture(this.maskAtlas);
        this.maskAtlas = maskTexture(this.gl, canvas.width, canvas.height, red);
        this.maskRasters = entries;
        if (this.maskOverlay >= LOCAL_RENDER_CAP) this.maskOverlay = -1;
        this.requestRender();
    }

    setMaskOverlay(indexOrNull) {
        if (indexOrNull == null || indexOrNull === false) this.maskOverlay = -1;
        else if (indexOrNull === true) this.maskOverlay = Number(this.maskRasters[0]?.correctionIndex ?? -1);
        else {
            const index = Number(indexOrNull);
            this.maskOverlay = Number.isInteger(index) && index >= 0 && index < LOCAL_RENDER_CAP ? index : -1;
        }
        this.requestRender();
    }

    setHealOverlay(show) {
        this.healOverlay = Boolean(show);
        this.requestRender();
    }

    requestRender() {
        this.dirty = true;
        if (!this.ready) return;
        if (!this.frame) this.frame = requestAnimationFrame(() => this.render());
    }

    bindFilmUnits() {
        bindUnit(this.gl, this.filmHd, 8);
        bindUnit(this.gl, this.filmPrint, 9);
        bindUnit(this.gl, this.filmGlow?.texture, 10);
    }

    bindDngUnits() {
        bindUnit(this.gl, this.dngHueSat, 11);
        bindUnit(this.gl, this.dngLook, 12);
        bindUnit(this.gl, this.dngTone, 13);
    }

    setProgramView(program, view = null) {
        const selected = view || { scale: 1, center: { u: .5, v: .5 } };
        const scale = this.gl.getUniformLocation(program, 'u_viewScale');
        const center = this.gl.getUniformLocation(program, 'u_viewCenter');
        if (scale) this.gl.uniform1f(scale, selected.scale);
        if (center) this.gl.uniform2f(center, selected.center.u, selected.center.v);
    }

    uniforms(p) {
        const gl = this.gl;
        const s = this.settings;
        const uniform = (name) => gl.getUniformLocation(p, name);
        gl.uniform1i(uniform('u_source'), 0);
        gl.uniform1i(uniform('u_curve'), 1);
        gl.uniform1i(uniform('u_baseCurve'), 5);
        gl.uniform1i(uniform('u_filmHd'), 8);
        gl.uniform1i(uniform('u_filmPrint'), 9);
        gl.uniform1i(uniform('u_filmGlow'), 10);
        gl.uniform1i(uniform('u_dngHueSat'), 11);
        gl.uniform1i(uniform('u_dngLook'), 12);
        gl.uniform1i(uniform('u_dngTone'), 13);
        gl.uniform2f(uniform('u_sourceSize'), this.width, this.height);
        const film = this.filmTables;
        const filmActive = this.filmActive();
        const identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
        const halation = film?.halation || {};
        const grain = film?.grain || {};
        const filmPercent = (key) => Math.max(0, Math.min(1, numberSetting(s, key, 100) / 100));
        const minSide = Math.min(this.width, this.height);
        gl.uniform1i(uniform('u_filmActive'), filmActive ? 1 : 0);
        gl.uniform1i(uniform('u_filmBw'), film?.bw ? 1 : 0);
        gl.uniform1i(uniform('u_filmNegative'), film?.negative ? 1 : 0);
        gl.uniformMatrix3fv(uniform('u_filmCrosstalk'), false, matrixColumnMajor(film?.crosstalk || identity));
        gl.uniformMatrix3fv(uniform('u_filmDir'), false, matrixColumnMajor(film?.dir || identity));
        gl.uniformMatrix3fv(uniform('u_filmScan'), false, matrixColumnMajor(film?.scan || identity));
        gl.uniform3fv(uniform('u_filmMask'), film?.mask || new Float32Array(3));
        gl.uniform3fv(uniform('u_filmDRef'), film?.dRef || new Float32Array(3));
        gl.uniform4f(
            uniform('u_filmHalation'),
            Number(halation.amount || 0) * filmPercent('pa_FilmHalation'),
            Number(halation.threshold ?? 0.7),
            Number(halation.radiusFrac ?? 0.015),
            Number(halation.greenFraction ?? 0.2),
        );
        gl.uniform3f(
            uniform('u_filmGrain'),
            Number(grain.rms || 0) / 1000 * 9 * filmPercent('pa_FilmGrain'),
            Math.max(Number(grain.sizePxAt4k || 1) * filmPercent('pa_FilmGrainSize') * (minSide / 4000), 1),
            Number(grain.shadowBias ?? 0.35),
        );
        gl.uniform1f(uniform('u_filmStrength'), filmPercent('pa_FilmStrength'));
        const colorProfile = this.colorProfile || canvasColorProfile(this.meta);
        const color = colorProfile.color || colorProfile;
        const adobe = colorProfile.adobe_profile || colorProfile.color?.adobe_profile || null;
        gl.uniform1i(uniform('u_dngActive'), adobe ? 1 : 0);
        gl.uniform1i(uniform('u_dngHueActive'), adobe && this.dngHueDims[0] ? 1 : 0);
        gl.uniform1i(uniform('u_dngLookActive'), adobe && this.dngLookDims[0] ? 1 : 0);
        gl.uniform3iv(uniform('u_dngHueDims'), new Int32Array(this.dngHueDims));
        gl.uniform3iv(uniform('u_dngLookDims'), new Int32Array(this.dngLookDims));
        gl.uniform1i(uniform('u_dngHueEncoding'), Number(adobe?.hue_sat_map_encoding || 0));
        gl.uniform1i(uniform('u_dngLookEncoding'), Number(adobe?.look_table_encoding || 0));
        gl.uniformMatrix3fv(uniform('u_dngSrgbToProPhoto'), false, matrixColumnMajor(DNG_LINEAR_SRGB_TO_PROPHOTO));
        gl.uniformMatrix3fv(uniform('u_dngProPhotoToSrgb'), false, matrixColumnMajor(DNG_PROPHOTO_TO_LINEAR_SRGB));
        const baseIncludesBaselineExposure = color.forward_matrix != null;
        gl.uniform1f(uniform('u_dngBaselineExposure'), baseIncludesBaselineExposure ? 0 : Number(adobe?.baseline_exposure || 0));
        const displayBase = (colorProfile.base_kind || this.meta.base_kind) === 'display';
        const profile = displayBase ? null : (colorProfile.camera_profile || colorProfile.color?.camera_profile || null);
        const profileTable = new Float32Array(CAMERA_PROFILE_HUE_BINS * CAMERA_PROFILE_CHROMA_BINS * 2);
        if (Array.isArray(profile?.oklab_ab_delta)) {
            let cursor = 0;
            for (const hueRow of profile.oklab_ab_delta.slice(0, CAMERA_PROFILE_HUE_BINS)) {
                for (const delta of (Array.isArray(hueRow) ? hueRow : []).slice(0, CAMERA_PROFILE_CHROMA_BINS)) {
                    profileTable[cursor] = Number(delta?.[0]) || 0;
                    profileTable[cursor + 1] = Number(delta?.[1]) || 0;
                    cursor += 2;
                }
            }
        }
        const profileEdges = Array.isArray(profile?.chroma_edges)
            && profile.chroma_edges.length === CAMERA_PROFILE_CHROMA_BINS + 1
            ? profile.chroma_edges.map(Number) : [0.02, 0.06, 0.12, 1.0];
        gl.uniform1f(uniform('u_baseProfileSat'), displayBase || profile || adobe ? 1 : BASE_PROFILE_SAT);
        gl.uniform1i(uniform('u_cameraProfile'), profile ? 1 : 0);
        gl.uniform2fv(uniform('u_cameraAbDelta[0]'), profileTable);
        gl.uniform1fv(uniform('u_cameraChromaEdges[0]'), new Float32Array(profileEdges));
        const lens = this.meta.lens_correction || this.meta.color?.lens_correction || null;
        const lensEnabled = boolSetting(s, 'LensProfileEnable') && !this.meta.hdr && !!lens;
        const distortion = lens?.distortion || null;
        const lensModel = { poly3: 1, poly5: 2, ptlens: 3 }[String(distortion?.model || '').toLowerCase()] || 0;
        const lensTerms = Array.isArray(distortion?.terms) ? distortion.terms.map(Number) : [0, 0, 0];
        const lensVignetting = lens?.vignetting?.model === 'pa' ? lens.vignetting : null;
        const vignetteTerms = Array.isArray(lensVignetting?.terms) ? lensVignetting.terms.map(Number) : [0, 0, 0];
        const cameraCrop = Number(lens?.camera_crop_factor) || 1;
        const lensCrop = Number(lens?.lens_crop_factor) || 1;
        const lensDistortionScale = Math.max(LENS_PROFILE_SCALE_MIN, Math.min(LENS_PROFILE_SCALE_MAX,
            numberSetting(s, 'LensProfileDistortionScale', LENS_PROFILE_SCALE_DEFAULT))) / 100;
        const lensVignettingScale = Math.max(LENS_PROFILE_SCALE_MIN, Math.min(LENS_PROFILE_SCALE_MAX,
            numberSetting(s, 'LensProfileVignettingScale', LENS_PROFILE_SCALE_DEFAULT))) / 100;
        gl.uniform1i(uniform('u_lensProfile'), lensEnabled && lensModel ? 1 : 0);
        gl.uniform1i(uniform('u_lensModel'), lensModel);
        gl.uniform3f(uniform('u_lensTerms'), lensTerms[0] || 0, lensTerms[1] || 0, lensTerms[2] || 0);
        gl.uniform1i(uniform('u_lensVignetting'), lensEnabled && lensVignetting ? 1 : 0);
        gl.uniform3f(uniform('u_lensVignetteTerms'), vignetteTerms[0] || 0, vignetteTerms[1] || 0, vignetteTerms[2] || 0);
        gl.uniform1f(uniform('u_lensCropRatio'), lensCrop / cameraCrop);
        gl.uniform1f(uniform('u_lensAutoCrop'), lensEnabled && lensModel ? lensAutoCropScale(distortion, this.width, this.height, lensCrop / cameraCrop, lensDistortionScale) : 1);
        gl.uniform1f(uniform('u_lensDistortionScale'), lensDistortionScale);
        gl.uniform1f(uniform('u_lensVignettingScale'), lensVignettingScale);
        gl.uniform1f(uniform('u_lensManualDistortion'), numberSetting(s, 'LensManualDistortionAmount') / 100);
        gl.uniform1f(uniform('u_lensManualVignette'), numberSetting(s, 'LensManualVignetteAmount') / 100);
        gl.uniform1f(uniform('u_lensManualVignetteMidpoint'), numberSetting(s, 'LensManualVignetteMidpoint', 50));
        const asShotT = Number(this.meta.as_shot_temperature || this.meta.temperature || 5500);
        const asShotTint = Number(this.meta.as_shot_tint || 0);
        const userT = numberSetting(s, 'Temperature', asShotT);
        const userTint = numberSetting(s, 'Tint');
        let wb = wbMatrix(color, asShotT, asShotTint, userT, userTint);
        if (!wb) {
            const gains = wbGains(asShotT, asShotTint, userT, userTint);
            wb = [[gains[0], 0, 0], [0, gains[1], 0], [0, 0, gains[2]]];
        }
        gl.uniformMatrix3fv(uniform('u_wbMatrix'), true, new Float32Array([
            wb[0][0], wb[0][1], wb[0][2],
            wb[1][0], wb[1][1], wb[1][2],
            wb[2][0], wb[2][1], wb[2][2],
        ]));
        gl.uniform1i(uniform('u_applyWb'), s.WhiteBalance !== 'As Shot' && s.Temperature != null ? 1 : 0);
        gl.uniformMatrix3fv(uniform('u_calibrationMatrix'), false, calibrationMatrix(s));
        gl.uniform1f(uniform('u_calibrationShadowTint'), numberSetting(s, 'CalibrationShadowTint') / 100);
        gl.uniform1f(uniform('u_exposure'), numberSetting(s, 'Exposure2012'));
        gl.uniform1f(uniform('u_contrast'), numberSetting(s, 'Contrast2012'));
        gl.uniform4f(uniform('u_regions'), numberSetting(s, 'Highlights2012'), numberSetting(s, 'Shadows2012'), numberSetting(s, 'Whites2012'), numberSetting(s, 'Blacks2012'));
        gl.uniform1f(uniform('u_dehaze'), numberSetting(s, 'Dehaze'));
        gl.uniform1f(uniform('u_vibrance'), numberSetting(s, 'Vibrance'));
        gl.uniform1f(uniform('u_saturation'), numberSetting(s, 'Saturation'));
        gl.uniform1fv(uniform('u_hue[0]'), new Float32Array(BAND_NAMES.map((name) => numberSetting(s, `HueAdjustment${name}`))));
        gl.uniform1fv(uniform('u_hslSat[0]'), new Float32Array(BAND_NAMES.map((name) => numberSetting(s, `SaturationAdjustment${name}`))));
        gl.uniform1fv(uniform('u_hslLum[0]'), new Float32Array(BAND_NAMES.map((name) => numberSetting(s, `LuminanceAdjustment${name}`))));
        gl.uniform1fv(uniform('u_gray[0]'), new Float32Array(BAND_NAMES.map((name) => numberSetting(s, `GrayMixer${name}`))));
        gl.uniform1i(uniform('u_grayscale'), boolSetting(s, 'ConvertToGrayscale') ? 1 : 0);
        const useHsl = boolSetting(s, 'ConvertToGrayscale')
            || numberSetting(s, 'Dehaze') !== 0 || numberSetting(s, 'Vibrance') !== 0 || numberSetting(s, 'Saturation') !== 0
            || BAND_NAMES.some((name) => numberSetting(s, `HueAdjustment${name}`) !== 0
                || numberSetting(s, `SaturationAdjustment${name}`) !== 0
                || numberSetting(s, `LuminanceAdjustment${name}`) !== 0
                || numberSetting(s, `GrayMixer${name}`) !== 0);
        gl.uniform1i(uniform('u_useHsl'), useHsl ? 1 : 0);
        gl.uniform4f(uniform('u_crop'), numberSetting(s, 'CropLeft'), numberSetting(s, 'CropTop'), numberSetting(s, 'CropRight'), numberSetting(s, 'CropBottom'));
        gl.uniform1f(uniform('u_angle'), numberSetting(s, 'CropAngle'));
        gl.uniform1i(uniform('u_orientation'), numberSetting(s, 'Orientation', 1));
        gl.uniform1i(uniform('u_applyGeometry'), this.geometryEnabled ? 1 : 0);
        gl.uniformMatrix3fv(uniform('u_transformInverse'), false, transformInverseColumnMajor(s));
        const grade = (name) => [
            numberSetting(s, `ColorGrade${name}Hue`), numberSetting(s, `ColorGrade${name}Sat`), numberSetting(s, `ColorGrade${name}Lum`),
        ];
        for (const name of ['Shadow', 'Midtone', 'Highlight', 'Global']) gl.uniform3fv(uniform(`u_grade${name}`), new Float32Array(grade(name)));
        gl.uniform1f(uniform('u_gradeBlending'), numberSetting(s, 'ColorGradeBlending', 50));
        gl.uniform1f(uniform('u_gradeBalance'), numberSetting(s, 'ColorGradeBalance'));
    }

    localUniforms() {
        const gl = this.gl;
        const p = this.mainProgram;
        const uniform = (name) => gl.getUniformLocation(p, name);
        const corrections = Array.isArray(this.settings.MaskGroupBasedCorrections)
            ? this.settings.MaskGroupBasedCorrections.slice(0, LOCAL_RENDER_CAP) : [];
        const active = new Int32Array(LOCAL_RENDER_CAP);
        const amount = new Float32Array(LOCAL_RENDER_CAP);
        const lightA = new Float32Array(LOCAL_RENDER_CAP * 4);
        const lightB = new Float32Array(LOCAL_RENDER_CAP * 4);
        const effects = new Float32Array(LOCAL_RENDER_CAP * 4);
        const color = new Float32Array(LOCAL_RENDER_CAP * 4);
        for (let i = 0; i < corrections.length; i += 1) {
            const local = corrections[i] || {};
            active[i] = correctionEnabled(local) ? 1 : 0;
            amount[i] = Math.min(Math.max(numberSetting(local, 'CorrectionAmount', 1), 0), 2);
            lightA.set([
                localToSlider(local, 'LocalExposure2012'),
                localToSlider(local, 'LocalContrast2012') / 100,
                localToSlider(local, 'LocalHighlights2012') / 100,
                localToSlider(local, 'LocalShadows2012') / 100,
            ], i * 4);
            lightB.set([
                localToSlider(local, 'LocalWhites2012') / 100,
                localToSlider(local, 'LocalBlacks2012') / 100,
                localToSlider(local, 'LocalClarity2012') / 100,
                localToSlider(local, 'LocalDehaze') / 100,
            ], i * 4);
            effects.set([
                localToSlider(local, 'LocalTexture') / 100,
                localToSlider(local, 'LocalTemperature'),
                localToSlider(local, 'LocalTint'),
                localToSlider(local, 'LocalSaturation') / 100,
            ], i * 4);
            color[i * 4] = localToSlider(local, 'LocalHue') / 100 * LOCAL_HUE_DEGREES;
        }
        gl.uniform1iv(uniform('u_localActive[0]'), active);
        gl.uniform1fv(uniform('u_localAmount[0]'), amount);
        gl.uniform4fv(uniform('u_localLightA[0]'), lightA);
        gl.uniform4fv(uniform('u_localLightB[0]'), lightB);
        gl.uniform4fv(uniform('u_localEffects[0]'), effects);
        gl.uniform4fv(uniform('u_localColor[0]'), color);
        gl.uniform1i(uniform('u_maskOverlay'), this.maskOverlay);
        gl.uniform1i(uniform('u_maskAtlas'), 6);
    }

    retouchUniforms(spots) {
        const gl = this.gl;
        const uniform = (name) => gl.getUniformLocation(this.retouchProgram, name);
        const active = new Int32Array(RETOUCH_RENDER_CAP);
        const sourceData = new Float32Array(RETOUCH_RENDER_CAP * 4);
        const destinationData = new Float32Array(RETOUCH_RENDER_CAP * 4);
        spots.forEach((spot, index) => {
            active[index] = 1;
            sourceData.set([spot.src_x, spot.src_y, spot.radius, spot.feather], index * 4);
            destinationData.set([spot.dst_x, spot.dst_y, spot.opacity, spot.mode === 'heal' ? 1 : 0], index * 4);
        });
        gl.uniform1i(uniform('u_retouchSource'), 0);
        gl.uniform1iv(uniform('u_retouchActive[0]'), active);
        gl.uniform4fv(uniform('u_retouchSourceData[0]'), sourceData);
        gl.uniform4fv(uniform('u_retouchDestinationData[0]'), destinationData);
        gl.uniform1i(uniform('u_healOverlay'), this.healOverlay ? 1 : 0);
    }

    drawColorTarget() {
        const gl = this.gl;
        gl.useProgram(this.lumaProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.baseCurve, 5);
        this.bindFilmUnits();
        this.bindDngUnits();
        this.uniforms(this.lumaProgram);
        this.setProgramView(this.lumaProgram);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.lumaTarget.framebuffer);
        gl.viewport(0, 0, this.lumaTarget.width, this.lumaTarget.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    drawFilmLumaTarget() {
        const gl = this.gl;
        gl.useProgram(this.filmLumaProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.baseCurve, 5);
        this.bindFilmUnits();
        this.bindDngUnits();
        this.uniforms(this.filmLumaProgram);
        this.setProgramView(this.filmLumaProgram);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.filmLumaTarget.framebuffer);
        gl.viewport(0, 0, this.filmLumaTarget.width, this.filmLumaTarget.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    bindPreDetailUniforms(program) {
        const gl = this.gl;
        const uniform = (name) => gl.getUniformLocation(program, name);
        gl.uniform1i(uniform('u_blurSharp'), 4);
        gl.uniform1i(uniform('u_lumaNr'), 7);
        gl.uniform1f(uniform('u_luminanceSmoothing'), numberSetting(this.settings, 'LuminanceSmoothing'));
        gl.uniform1f(uniform('u_luminanceDetail'), numberSetting(this.settings, 'LuminanceDetail'));
        gl.uniform1f(uniform('u_luminanceContrast'), numberSetting(this.settings, 'LuminanceContrast'));
        const defringe = (name, defaults) => gl.uniform4f(uniform(`u_defringe${name}`),
            Math.max(0, Math.min(1, numberSetting(this.settings, `Defringe${name}Amount`) / 100)),
            numberSetting(this.settings, `Defringe${name}HueLo`, defaults[0]), numberSetting(this.settings, `Defringe${name}HueHi`, defaults[1]), 0);
        defringe('Purple', [30, 70]);
        defringe('Green', [40, 60]);
    }

    drawPreDetailTarget() {
        const gl = this.gl;
        gl.useProgram(this.preDetailProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.blurSharp.texture, 4);
        bindUnit(gl, this.baseCurve, 5);
        bindUnit(gl, this.lumaTarget.texture, 7);
        this.bindFilmUnits();
        this.bindDngUnits();
        this.uniforms(this.preDetailProgram);
        this.bindPreDetailUniforms(this.preDetailProgram);
        this.setProgramView(this.preDetailProgram);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.preDetailTarget.framebuffer);
        gl.viewport(0, 0, this.width, this.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    drawGeometryTarget() {
        const gl = this.gl;
        const uniform = (name) => gl.getUniformLocation(this.geometryProgram, name);
        gl.useProgram(this.geometryProgram);
        bindUnit(gl, this.processedTarget.texture, 0);
        gl.uniform1i(uniform('u_developed'), 0);
        gl.uniform2f(uniform('u_sourceSize'), this.width, this.height);
        gl.uniform4f(uniform('u_crop'), numberSetting(this.settings, 'CropLeft'), numberSetting(this.settings, 'CropTop'), numberSetting(this.settings, 'CropRight'), numberSetting(this.settings, 'CropBottom'));
        gl.uniform1f(uniform('u_angle'), numberSetting(this.settings, 'CropAngle'));
        gl.uniform1i(uniform('u_orientation'), numberSetting(this.settings, 'Orientation', 1));
        gl.uniform1i(uniform('u_applyGeometry'), this.geometryEnabled ? 1 : 0);
        gl.uniformMatrix3fv(uniform('u_transformInverse'), false, transformInverseColumnMajor(this.settings));
        this.setProgramView(this.geometryProgram, this.view);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, this.canvas.width, this.canvas.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    blur(sigma, output, source = this.lumaTarget, temp = this.blurTemp, downsample = 2) {
        const gl = this.gl;
        gl.useProgram(this.blurProgram);
        this.setProgramView(this.blurProgram);
        gl.uniform1i(gl.getUniformLocation(this.blurProgram, 'u_image'), 0);
        gl.uniform1f(gl.getUniformLocation(this.blurProgram, 'u_sigma'), Math.max(.5, sigma / downsample));
        bindUnit(gl, source.texture, 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, temp.framebuffer);
        gl.viewport(0, 0, temp.width, temp.height);
        gl.uniform2f(gl.getUniformLocation(this.blurProgram, 'u_direction'), 1 / temp.width, 0);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        bindUnit(gl, temp.texture, 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, output.framebuffer);
        gl.uniform2f(gl.getUniformLocation(this.blurProgram, 'u_direction'), 0, 1 / temp.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    render() {
        this.frame = 0;
        if (!this.dirty || !this.ready) return;
        this.dirty = false;
        const gl = this.gl;
        gl.bindVertexArray(this.vao);
        if (this.displayPreview && this.previewSource) {
            gl.useProgram(this.displayPreviewProgram);
            bindUnit(gl, this.previewSource, 0);
            gl.uniform1i(gl.getUniformLocation(this.displayPreviewProgram, 'u_preview'), 0);
            this.setProgramView(this.displayPreviewProgram, this.view);
            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            gl.viewport(0, 0, this.canvas.width, this.canvas.height);
            gl.drawArrays(gl.TRIANGLES, 0, 6);
            this.canvas.dispatchEvent(new CustomEvent('develop:rendered'));
            return;
        }
        if (!this.targets?.length) return;
        const clarity = numberSetting(this.settings, 'Clarity2012');
        const textureValue = numberSetting(this.settings, 'Texture');
        const sharpness = numberSetting(this.settings, 'Sharpness');
        const localCorrections = Array.isArray(this.settings.MaskGroupBasedCorrections)
            ? this.settings.MaskGroupBasedCorrections.slice(0, LOCAL_RENDER_CAP).filter(correctionEnabled) : [];
        const localClarity = localCorrections.some((correction) => numberSetting(correction, 'LocalClarity2012') !== 0);
        const localTexture = localCorrections.some((correction) => numberSetting(correction, 'LocalTexture') !== 0);
        const defringe = numberSetting(this.settings, 'DefringePurpleAmount') !== 0 || numberSetting(this.settings, 'DefringeGreenAmount') !== 0;
        const nr = numberSetting(this.settings, 'LuminanceSmoothing') !== 0 || numberSetting(this.settings, 'ColorNoiseReduction') !== 0;
        const spots = retouchSpots(this.settings);
        const filmActive = this.filmActive();
        if (filmActive && numberSetting(this.settings, 'pa_FilmHalation', 100) > 0 && Number(this.filmTables.halation.amount) > 0) {
            this.drawFilmLumaTarget();
            this.blur(
                Number(this.filmTables.halation.radiusFrac) * Math.min(this.width, this.height),
                this.filmGlow,
                this.filmLumaTarget,
                this.filmBlurTemp,
                8,
            );
        }
        const useBlur = clarity !== 0 || textureValue !== 0 || sharpness > 0 || localClarity || localTexture || defringe || nr;
        if (useBlur) {
            this.drawColorTarget();
            const minSide = Math.min(this.width, this.height);
            if (clarity !== 0 || localClarity) this.blur(BLUR_LARGE_FACTOR * minSide, this.blurLarge);
            if (textureValue !== 0 || localTexture) this.blur(BLUR_SMALL_FACTOR * minSide, this.blurSmall);
            if (sharpness > 0 || defringe || nr) this.blur(sharpness > 0 ? numberSetting(this.settings, 'SharpenRadius', 1) : 1, this.blurSharp);
        }
        const usePreDetail = defringe || nr;
        if (usePreDetail) this.drawPreDetailTarget();
        gl.useProgram(this.mainProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.blurLarge.texture, 2);
        bindUnit(gl, this.blurSmall.texture, 3);
        bindUnit(gl, this.blurSharp.texture, 4);
        bindUnit(gl, this.baseCurve, 5);
        bindUnit(gl, this.maskAtlas, 6);
        bindUnit(gl, this.lumaTarget.texture, 7);
        bindUnit(gl, this.preDetailTarget.texture, 14);
        this.bindFilmUnits();
        this.bindDngUnits();
        this.uniforms(this.mainProgram);
        this.setProgramView(this.mainProgram);
        this.localUniforms();
        const uniform = (name) => gl.getUniformLocation(this.mainProgram, name);
        gl.uniform1i(uniform('u_blurLarge'), 2);
        gl.uniform1i(uniform('u_blurSmall'), 3);
        gl.uniform1i(uniform('u_blurSharp'), 4);
        gl.uniform1i(uniform('u_lumaNr'), 7);
        gl.uniform1i(uniform('u_preDetail'), 14);
        gl.uniform1i(uniform('u_preDetailActive'), usePreDetail ? 1 : 0);
        gl.uniform1i(uniform('u_useLarge'), clarity !== 0 ? 1 : 0);
        gl.uniform1i(uniform('u_useSmall'), textureValue !== 0 ? 1 : 0);
        gl.uniform1i(uniform('u_useSharp'), sharpness > 0 ? 1 : 0);
        gl.uniform1f(uniform('u_clarity'), clarity);
        gl.uniform1f(uniform('u_texture'), textureValue);
        gl.uniform1f(uniform('u_sharpness'), sharpness);
        gl.uniform1f(uniform('u_sharpenMasking'), numberSetting(this.settings, 'SharpenEdgeMasking'));
        gl.uniform1f(uniform('u_vignette'), numberSetting(this.settings, 'PostCropVignetteAmount'));
        gl.uniform3f(uniform('u_vignetteShape'), numberSetting(this.settings, 'PostCropVignetteMidpoint', 50), numberSetting(this.settings, 'PostCropVignetteFeather', 50), numberSetting(this.settings, 'PostCropVignetteRoundness'));
        gl.uniform1f(uniform('u_grain'), numberSetting(this.settings, 'GrainAmount'));
        gl.uniform1f(uniform('u_grainSize'), numberSetting(this.settings, 'GrainSize', 25));
        gl.uniform1f(uniform('u_grainFrequency'), numberSetting(this.settings, 'GrainFrequency', 50));
        gl.uniform1f(uniform('u_colorNoiseReduction'), numberSetting(this.settings, 'ColorNoiseReduction'));
        this.bindPreDetailUniforms(this.mainProgram);
        const proofProfile = { off: 0, srgb: 1, 'adobe-rgb': 2, 'display-p3': 3, paper: 4 }[this.softProof?.profile] ?? 0;
        const [proofToXyz, xyzToProof] = proofMatrices(this.softProof?.profile);
        const columnMajor = (matrix) => new Float32Array([
            matrix[0][0], matrix[1][0], matrix[2][0], matrix[0][1], matrix[1][1], matrix[2][1], matrix[0][2], matrix[1][2], matrix[2][2],
        ]);
        gl.uniform1i(uniform('u_softProofProfile'), proofProfile);
        gl.uniform1i(uniform('u_gamutWarning'), this.softProof?.warning ? 1 : 0);
        gl.uniformMatrix3fv(uniform('u_srgbToXyz'), false, columnMajor(SOFT_PROOF_SRGB_TO_XYZ));
        gl.uniformMatrix3fv(uniform('u_xyzToSrgb'), false, columnMajor(SOFT_PROOF_XYZ_TO_SRGB));
        gl.uniformMatrix3fv(uniform('u_proofToXyz'), false, columnMajor(proofToXyz));
        gl.uniformMatrix3fv(uniform('u_xyzToProof'), false, columnMajor(xyzToProof));
        gl.uniform1ui(uniform('u_seed'), GRAIN_SEED >>> 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, spots.length ? this.retouchTarget.framebuffer : this.processedTarget.framebuffer);
        gl.viewport(0, 0, this.width, this.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        if (spots.length) {
            gl.useProgram(this.retouchProgram);
            this.setProgramView(this.retouchProgram);
            bindUnit(gl, this.retouchTarget.texture, 0);
            this.retouchUniforms(spots);
            gl.bindFramebuffer(gl.FRAMEBUFFER, this.processedTarget.framebuffer);
            gl.viewport(0, 0, this.width, this.height);
            gl.drawArrays(gl.TRIANGLES, 0, 6);
        }
        this.drawGeometryTarget();
        this.canvas.dispatchEvent(new CustomEvent('develop:rendered'));
        markFrameDone();
    }

    readPixels(width = this.canvas.width, height = this.canvas.height) {
        this.render();
        const pixels = new Uint8Array(width * height * 4);
        this.gl.readPixels(0, 0, width, height, this.gl.RGBA, this.gl.UNSIGNED_BYTE, pixels);
        const error = this.gl.getError();
        if (error !== this.gl.NO_ERROR) throw new Error(`Develop readback failed (gl 0x${error.toString(16)})`);
        return pixels;
    }

    destroy() {
        try { this.gl.getExtension('WEBGL_lose_context')?.loseContext(); } catch { /* already lost */ }
        if (this.frame) cancelAnimationFrame(this.frame);
        const gl = this.gl;
        for (const item of this.targets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        for (const item of this.filmTargets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        for (const item of [this.retouchTarget, this.preDetailTarget, this.processedTarget]) if (item) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        gl.deleteTexture(this.source);
        gl.deleteTexture(this.curve);
        if (this.baseCurve) gl.deleteTexture(this.baseCurve);
        if (this.filmHd) gl.deleteTexture(this.filmHd);
        if (this.filmPrint) gl.deleteTexture(this.filmPrint);
        if (this.maskAtlas) gl.deleteTexture(this.maskAtlas);
        if (this.previewSource) gl.deleteTexture(this.previewSource);
    }
}

export function syntheticLinearRgba(size = 64) {
    const data = new Float32Array(size * size * 4);
    for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
            const i = (y * size + x) * 4;
            const hue = ((x * (360 / Math.max(1, size - 1)) + y * .75) % 360) / 360;
            const saturation = .15 + .85 * x / Math.max(1, size - 1);
            const value = .03 + .97 * y / Math.max(1, size - 1);
            const rgb = (() => {
                const p = Math.floor(hue * 6);
                const f = hue * 6 - p;
                const q = value * (1 - saturation * f);
                const t = value * (1 - saturation * (1 - f));
                const low = value * (1 - saturation);
                return [[value, t, low], [q, value, low], [low, value, t], [low, q, value], [t, low, value], [value, low, q]][p % 6];
            })();
            data.set([...rgb, 1], i);
        }
    }
    return data;
}

export async function renderSyntheticPixels(settings = {}, meta = {}, options = {}) {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const renderer = new DevelopRenderer(canvas);
    renderer.geometryEnabled = Boolean(options.geometry);
    const sourceSize = Array.isArray(options.sourceSize) ? options.sourceSize : [64, 64];
    const source = Array.isArray(options.sourceRgba) ? new Float32Array(options.sourceRgba) : syntheticLinearRgba(64);
    renderer.uploadSource(source, Number(sourceSize[0]), Number(sourceSize[1]));
    if (Array.isArray(options.outputSize) && options.outputSize.length === 2) {
        renderer.canvas.width = Math.max(1, Number(options.outputSize[0]) || 1);
        renderer.canvas.height = Math.max(1, Number(options.outputSize[1]) || 1);
    }
    renderer.setSettings(settings, { as_shot_temperature: 5150, as_shot_tint: 0, ...meta });
    await renderer.waitForFilm();
    if (Array.isArray(settings.MaskGroupBasedCorrections)) {
        renderer.setMaskRasters(await buildMaskRasters({ corrections: settings.MaskGroupBasedCorrections, width: 64, height: 64 }));
    }
    renderer.render();
    const result = renderer.readPixels(renderer.canvas.width, renderer.canvas.height);
    renderer.destroy();
    return result;
}
