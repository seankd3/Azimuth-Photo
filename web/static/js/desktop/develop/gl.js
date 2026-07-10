import {
    BAND_NAMES, BASE_PROFILE_SAT, BLUR_LARGE_FACTOR, BLUR_SMALL_FACTOR, CLARITY_FACTOR,
    CLARITY_RESIDUAL_MAX, CONTRAST_FACTOR, DEHAZE_AIRLIGHT_FACTOR, DEHAZE_SATURATION_FACTOR,
    GRAIN_CELL_SIZE_MIN, GRAIN_CELL_SIZE_RANGE, GRAIN_FACTOR, GRAIN_HASH_MULTIPLIER,
    GRAIN_HASH_SHIFT, GRAIN_OUTPUT_MASK, GRAIN_OUTPUT_SHIFT, GRAIN_SEED,
    GRAIN_X_MULTIPLIER, GRAIN_Y_MULTIPLIER, GRAY_MIXER_FACTOR, HSL_LUMINANCE_FACTOR,
    HUE_SHIFT_DEGREES, LUMA_BLUE, LUMA_GREEN, LUMA_RED, TINT_UV_SCALE,
    LOCAL_HUE_DEGREES, LOCAL_MASK_ATLAS_COLUMNS, LOCAL_RENDER_CAP,
    LOCAL_WB_TEMP_FACTOR, LOCAL_WB_TINT_FACTOR,
    OKLAB_C_NORM, OKLAB_M1, OKLAB_M1_INV, OKLAB_M2, OKLAB_M2_INV,
    SHARPEN_FACTOR, SHARPEN_THRESHOLD, TEXTURE_FACTOR, TONE_BLACKS_FACTOR,
    TONE_EV_BLACKS_CENTER, TONE_EV_HIGHLIGHTS_CENTER, TONE_EV_SHADOWS_CENTER,
    TONE_EV_SIGMA, TONE_EV_WHITES_CENTER, TONE_HIGHLIGHTS_FACTOR,
    TONE_HIGHLIGHTS_POS_SCALE, TONE_SHADOWS_FACTOR, TONE_WHITES_FACTOR,
    VIBRANCE_FACTOR, VIGNETTE_FACTOR, VIGNETTE_FEATHER_MIN,
    VIGNETTE_FEATHER_RANGE, VIGNETTE_MIDPOINT_MIN, VIGNETTE_MIDPOINT_RANGE,
    VIGNETTE_ROUNDNESS_FACTOR, boolSetting, numberSetting,
} from './ops_constants.js';
import { buildBaseProfileLut, buildCombinedCurveTexture } from './curve_lut.js';
import { buildMaskRasters, localToSlider } from './mask_raster.js';

/** Emit a GLSL float literal (JS 2.0 stringifies as "2", which GLSL treats as int). */
const f = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '0.0';
    const text = String(n);
    return /[eE.]/.test(text) ? text : `${text}.0`;
};

const correctionEnabled = (correction) => correction?.CorrectionActive == null
    || !['false', '0'].includes(String(correction.CorrectionActive).toLowerCase());

const VERTEX = `#version 300 es
in vec2 a_position;
out vec2 v_uv;
void main() {
    v_uv = a_position * .5 + .5;
    gl_Position = vec4(a_position, 0.0, 1.0);
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
uniform vec2 u_sourceSize;
uniform mat3 u_wbMatrix;
uniform bool u_applyWb;
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
`;

const COLOR_FUNCTION = `
vec2 orientedUv(vec2 uv) {
    if (!u_applyGeometry) return uv;
    if (u_orientation == 3) uv = vec2(1.0) - uv;
    else if (u_orientation == 6) uv = vec2(uv.y, 1.0 - uv.x);
    else if (u_orientation == 8) uv = vec2(1.0 - uv.y, uv.x);
    vec2 cropSize = max(u_crop.zw - u_crop.xy, vec2(.0001));
    vec2 p = u_crop.xy + uv * cropSize;
    vec2 center = (u_crop.xy + u_crop.zw) * .5;
    vec2 q = p - center;
    float aspect = u_sourceSize.x / max(u_sourceSize.y, 1.0);
    q.x *= aspect;
    float a = radians(u_angle);
    q = mat2(cos(a), -sin(a), sin(a), cos(a)) * q;
    q.x /= aspect;
    return center + q;
}
vec3 applyColor(vec2 uv, out vec2 imageUv) {
    imageUv = orientedUv(uv);
    if (any(lessThan(imageUv, vec2(0.0))) || any(greaterThan(imageUv, vec2(1.0)))) return vec3(0.0);
    vec3 rgb = texture(u_source, imageUv).rgb;
    if (u_applyWb) rgb = max(u_wbMatrix * rgb, vec3(0.0));
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
    vec3 c = linearToSrgb(clamp(rgb, 0.0, 1.0));
    c = vec3(texture(u_baseCurve, vec2(c.r, .5)).r, texture(u_baseCurve, vec2(c.g, .5)).r, texture(u_baseCurve, vec2(c.b, .5)).r);
    c = scaleOklabChroma(c, ${f(BASE_PROFILE_SAT)}, 0.0, 0.0, 0.0);
    vec3 mainCurve = vec3(texture(u_curve, vec2(c.r, .5)).r, texture(u_curve, vec2(c.g, .5)).r, texture(u_curve, vec2(c.b, .5)).r);
    c = vec3(texture(u_curve, vec2(mainCurve.r, .5)).g, texture(u_curve, vec2(mainCurve.g, .5)).b, texture(u_curve, vec2(mainCurve.b, .5)).a);
    if (!u_useHsl) return c;
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
    return c;
}
`;

const LUMA_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
${COLOR_UNIFORMS}
${COLOR_MATH}
${COLOR_FUNCTION}
void main() {
    vec2 imageUv;
    vec3 c = applyColor(v_uv, imageUv);
    outColor = vec4(vec3(dot(c, LUMW)), 1.0);
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
uniform uint u_seed;
uniform sampler2D u_maskAtlas;
uniform int u_localActive[${LOCAL_RENDER_CAP}];
uniform float u_localAmount[${LOCAL_RENDER_CAP}];
uniform vec4 u_localLightA[${LOCAL_RENDER_CAP}];
uniform vec4 u_localLightB[${LOCAL_RENDER_CAP}];
uniform vec4 u_localEffects[${LOCAL_RENDER_CAP}];
uniform vec4 u_localColor[${LOCAL_RENDER_CAP}];
uniform int u_maskOverlay;
${COLOR_MATH}
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
void main() {
    vec2 imageUv;
    vec3 c = applyColor(v_uv, imageUv);
    float L = dot(c, LUMW);
    vec2 localBlurs = vec2(texture(u_blurLarge, v_uv).r, texture(u_blurSmall, v_uv).r);
    for (int i = 0; i < ${LOCAL_RENDER_CAP}; i++) {
        if (u_localActive[i] == 0) continue;
        float m = clamp(localMask(i, imageUv) * u_localAmount[i], 0.0, 2.0);
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
        c += vec3(sign(r) * gated * (u_sharpness / 150.0) * ${f(SHARPEN_FACTOR)});
    }
    float a = setting(u_vignette);
    if (abs(a) > 1e-5) {
        vec2 center = (u_crop.xy + u_crop.zw) * .5;
        float cropAspect = (u_crop.z - u_crop.x) * u_sourceSize.x / max((u_crop.w - u_crop.y) * u_sourceSize.y, 1.0);
        float roundness = 1.0 + setting(u_vignetteShape.z) * ${f(VIGNETTE_ROUNDNESS_FACTOR)};
        vec2 p = imageUv - center;
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
        ivec2 pixel = ivec2(floor(imageUv * u_sourceSize / cell));
        c += vec3((noiseHash(pixel) - .5) * setting(u_grain) * ${f(GRAIN_FACTOR)});
    }
    if (u_maskOverlay >= 0) {
        float overlay = clamp(localMask(u_maskOverlay, imageUv), 0.0, 1.0) * .5;
        c = mix(c, vec3(1.0, 0.0, 0.0), overlay);
    }
    outColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;

function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const message = gl.getShaderInfoLog(shader);
        gl.deleteShader(shader);
        throw new Error(`Develop shader compile failed: ${message}`);
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
        this.lumaProgram = program(this.gl, LUMA_FRAGMENT);
        this.blurProgram = program(this.gl, BLUR_FRAGMENT);
        this.settings = {};
        this.meta = {};
        this.geometryEnabled = true;
        this.width = 1;
        this.height = 1;
        this.dirty = false;
        this.ready = false;
        this.frame = 0;
        this.createGeometry();
        this.source = texture(this.gl, 1, 1, {
            data: float32ToHalf(new Float32Array([0, 0, 0, 1])),
            internalFormat: this.gl.RGBA16F, type: this.gl.HALF_FLOAT,
        });
        this.canvas.width = 1;
        this.canvas.height = 1;
        this.baseCurve = null;
        this.maskAtlas = maskTexture(this.gl, LOCAL_MASK_ATLAS_COLUMNS, LOCAL_MASK_ATLAS_COLUMNS, new Uint8Array(LOCAL_MASK_ATLAS_COLUMNS ** 2));
        this.maskRasters = [];
        this.maskOverlay = -1;
        this.updateBaseCurve();
        this.updateCurve({});
    }

    createGeometry() {
        const gl = this.gl;
        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);
        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
        for (const p of [this.mainProgram, this.lumaProgram, this.blurProgram]) {
            const location = gl.getAttribLocation(p, 'a_position');
            gl.enableVertexAttribArray(location);
            gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
        }
        this.vao = vao;
    }

    uploadSource(data, width, height) {
        const gl = this.gl;
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
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

    rebuildTargets() {
        const gl = this.gl;
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        const width = Math.max(1, Math.ceil(this.width / 2));
        const height = Math.max(1, Math.ceil(this.height / 2));
        const next = [target(gl, width, height), target(gl, width, height), target(gl, width, height), target(gl, width, height), target(gl, width, height)];
        for (const item of this.targets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        [this.lumaTarget, this.blurTemp, this.blurLarge, this.blurSmall, this.blurSharp] = next;
        this.targets = next;
    }

    updateBaseCurve() {
        const gl = this.gl;
        if (this.baseCurve) gl.deleteTexture(this.baseCurve);
        const lut = buildBaseProfileLut();
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
        const gl = this.gl;
        if (this.curve) gl.deleteTexture(this.curve);
        this.curve = texture(gl, 256, 1, {
            data: float32ToHalf(buildCombinedCurveTexture(settings)), filter: gl.LINEAR,
            internalFormat: gl.RGBA16F, type: gl.HALF_FLOAT,
        });
    }

    setSettings(settings, meta = this.meta, _opts = {}) {
        this.settings = settings || {};
        this.meta = meta || {};
        this.updateCurve(this.settings);
        this.requestRender();
    }

    setMaskRasters(rasters = []) {
        const entries = (Array.isArray(rasters) ? rasters : []).filter((entry) => {
            const index = Number(entry?.correctionIndex);
            return Number.isInteger(index) && index >= 0 && index < LOCAL_RENDER_CAP && entry.canvasOrImageData;
        });
        const sizes = entries.map((entry) => maskSourceSize(entry.canvasOrImageData));
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

    requestRender() {
        this.dirty = true;
        if (!this.ready) return;
        if (!this.frame) this.frame = requestAnimationFrame(() => this.render());
    }

    uniforms(p) {
        const gl = this.gl;
        const s = this.settings;
        const uniform = (name) => gl.getUniformLocation(p, name);
        gl.uniform1i(uniform('u_source'), 0);
        gl.uniform1i(uniform('u_curve'), 1);
        gl.uniform1i(uniform('u_baseCurve'), 5);
        gl.uniform2f(uniform('u_sourceSize'), this.width, this.height);
        const asShotT = Number(this.meta.as_shot_temperature || this.meta.temperature || 5500);
        const asShotTint = Number(this.meta.as_shot_tint || 0);
        const userT = numberSetting(s, 'Temperature', asShotT);
        const userTint = numberSetting(s, 'Tint');
        let wb = wbMatrix(this.meta.color, asShotT, asShotTint, userT, userTint);
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

    drawColorTarget() {
        const gl = this.gl;
        gl.useProgram(this.lumaProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.baseCurve, 5);
        this.uniforms(this.lumaProgram);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.lumaTarget.framebuffer);
        gl.viewport(0, 0, this.lumaTarget.width, this.lumaTarget.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    blur(sigma, output) {
        const gl = this.gl;
        gl.useProgram(this.blurProgram);
        gl.uniform1i(gl.getUniformLocation(this.blurProgram, 'u_image'), 0);
        gl.uniform1f(gl.getUniformLocation(this.blurProgram, 'u_sigma'), Math.max(.5, sigma / 2));
        bindUnit(gl, this.lumaTarget.texture, 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.blurTemp.framebuffer);
        gl.viewport(0, 0, this.blurTemp.width, this.blurTemp.height);
        gl.uniform2f(gl.getUniformLocation(this.blurProgram, 'u_direction'), 1 / this.blurTemp.width, 0);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        bindUnit(gl, this.blurTemp.texture, 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, output.framebuffer);
        gl.uniform2f(gl.getUniformLocation(this.blurProgram, 'u_direction'), 0, 1 / this.blurTemp.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    render() {
        this.frame = 0;
        if (!this.dirty || !this.ready || !this.targets?.length) return;
        this.dirty = false;
        const gl = this.gl;
        gl.bindVertexArray(this.vao);
        const clarity = numberSetting(this.settings, 'Clarity2012');
        const textureValue = numberSetting(this.settings, 'Texture');
        const sharpness = numberSetting(this.settings, 'Sharpness');
        const localCorrections = Array.isArray(this.settings.MaskGroupBasedCorrections)
            ? this.settings.MaskGroupBasedCorrections.slice(0, LOCAL_RENDER_CAP).filter(correctionEnabled) : [];
        const localClarity = localCorrections.some((correction) => numberSetting(correction, 'LocalClarity2012') !== 0);
        const localTexture = localCorrections.some((correction) => numberSetting(correction, 'LocalTexture') !== 0);
        const useBlur = clarity !== 0 || textureValue !== 0 || sharpness > 0 || localClarity || localTexture;
        if (useBlur) {
            this.drawColorTarget();
            const minSide = Math.min(this.width, this.height);
            if (clarity !== 0 || localClarity) this.blur(BLUR_LARGE_FACTOR * minSide, this.blurLarge);
            if (textureValue !== 0 || localTexture) this.blur(BLUR_SMALL_FACTOR * minSide, this.blurSmall);
            if (sharpness > 0) this.blur(numberSetting(this.settings, 'SharpenRadius', 1), this.blurSharp);
        }
        gl.useProgram(this.mainProgram);
        bindUnit(gl, this.source, 0);
        bindUnit(gl, this.curve, 1);
        bindUnit(gl, this.blurLarge.texture, 2);
        bindUnit(gl, this.blurSmall.texture, 3);
        bindUnit(gl, this.blurSharp.texture, 4);
        bindUnit(gl, this.baseCurve, 5);
        bindUnit(gl, this.maskAtlas, 6);
        this.uniforms(this.mainProgram);
        this.localUniforms();
        const uniform = (name) => gl.getUniformLocation(this.mainProgram, name);
        gl.uniform1i(uniform('u_blurLarge'), 2);
        gl.uniform1i(uniform('u_blurSmall'), 3);
        gl.uniform1i(uniform('u_blurSharp'), 4);
        gl.uniform1i(uniform('u_useLarge'), clarity !== 0 ? 1 : 0);
        gl.uniform1i(uniform('u_useSmall'), textureValue !== 0 ? 1 : 0);
        gl.uniform1i(uniform('u_useSharp'), sharpness > 0 ? 1 : 0);
        gl.uniform1f(uniform('u_clarity'), clarity);
        gl.uniform1f(uniform('u_texture'), textureValue);
        gl.uniform1f(uniform('u_sharpness'), sharpness);
        gl.uniform1f(uniform('u_vignette'), numberSetting(this.settings, 'PostCropVignetteAmount'));
        gl.uniform3f(uniform('u_vignetteShape'), numberSetting(this.settings, 'PostCropVignetteMidpoint', 50), numberSetting(this.settings, 'PostCropVignetteFeather', 50), numberSetting(this.settings, 'PostCropVignetteRoundness'));
        gl.uniform1f(uniform('u_grain'), numberSetting(this.settings, 'GrainAmount'));
        gl.uniform1f(uniform('u_grainSize'), numberSetting(this.settings, 'GrainSize', 25));
        gl.uniform1ui(uniform('u_seed'), GRAIN_SEED >>> 0);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, this.canvas.width, this.canvas.height);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        this.canvas.dispatchEvent(new CustomEvent('develop:rendered'));
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
        if (this.frame) cancelAnimationFrame(this.frame);
        const gl = this.gl;
        for (const item of this.targets || []) {
            gl.deleteFramebuffer(item.framebuffer);
            gl.deleteTexture(item.texture);
        }
        gl.deleteTexture(this.source);
        gl.deleteTexture(this.curve);
        if (this.baseCurve) gl.deleteTexture(this.baseCurve);
        if (this.maskAtlas) gl.deleteTexture(this.maskAtlas);
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

export async function renderSyntheticPixels(settings = {}) {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const renderer = new DevelopRenderer(canvas);
    renderer.geometryEnabled = false;
    renderer.uploadSource(syntheticLinearRgba(64), 64, 64);
    renderer.setSettings(settings, { as_shot_temperature: 5150, as_shot_tint: 0 });
    if (Array.isArray(settings.MaskGroupBasedCorrections)) {
        renderer.setMaskRasters(await buildMaskRasters({ corrections: settings.MaskGroupBasedCorrections, width: 64, height: 64 }));
    }
    renderer.render();
    const result = renderer.readPixels(64, 64);
    renderer.destroy();
    return result;
}
