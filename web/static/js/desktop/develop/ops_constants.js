// JavaScript twin of features/develop/ops_constants.py PARITY_TABLE.
// Keep every uppercase name/value aligned; UI defaults and helpers live below.
export const TINT_UV_SCALE = 3000.0;
export const LUMA_RED = 0.2126;
export const LUMA_GREEN = 0.7152;
export const LUMA_BLUE = 0.0722;
export const TONE_GAMMA = 2.2;
export const TONE_HIGHLIGHTS_FACTOR = 2.0;
export const TONE_SHADOWS_FACTOR = 1.5;
export const TONE_WHITES_FACTOR = 1.0;
export const TONE_BLACKS_FACTOR = 1.0;
export const TONE_EV_SIGMA = 1.8;
export const TONE_EV_HIGHLIGHTS_CENTER = 1.5;
export const TONE_EV_WHITES_CENTER = 3.0;
export const TONE_EV_SHADOWS_CENTER = -3.5;
export const TONE_EV_BLACKS_CENTER = -6.0;
export const TONE_HIGHLIGHTS_POS_SCALE = 0.55;
export const CONTRAST_FACTOR = 0.85;
export const SOFT_CLAMP_FACTOR = 4.0;
export const TONE_EPSILON = 1e-6;
export const DEHAZE_AIRLIGHT_FACTOR = 0.12;
export const DEHAZE_SATURATION_FACTOR = 0.15;
export const SRGB_LINEAR_THRESHOLD = 0.0031308;
export const SRGB_ENCODE_SCALE = 12.92;
export const SRGB_ENCODE_A = 1.055;
export const SRGB_ENCODE_B = 0.055;
export const SRGB_ENCODE_GAMMA = 1.0 / 2.4;
export const SRGB_DECODE_THRESHOLD = 0.04045;
export const SRGB_DECODE_SCALE = 1.0 / 12.92;
export const SRGB_DECODE_A = 0.055;
export const SRGB_DECODE_GAMMA = 2.4;
export const CURVE_LUT_SIZE = 256;
export const CURVE_MONOTONE_LIMIT = 3.0;
export const BASE_PROFILE_POINTS = Object.freeze([
    Object.freeze([0.0, 0.0]),
    Object.freeze([20.0, 20.0]),
    Object.freeze([40.0, 39.0]),
    Object.freeze([64.0, 81.0]),
    Object.freeze([96.0, 156.0]),
    Object.freeze([128.0, 205.0]),
    Object.freeze([176.0, 232.0]),
    Object.freeze([216.0, 245.0]),
    Object.freeze([255.0, 255.0]),
]);
export const BASE_PROFILE_SAT = 1.22;
export const OKLAB_M1 = Object.freeze([
    Object.freeze([0.4122214708, 0.5363325363, 0.0514459929]),
    Object.freeze([0.2119034982, 0.6806995451, 0.1073969566]),
    Object.freeze([0.0883024619, 0.2817188376, 0.6299787005]),
]);
export const OKLAB_M2 = Object.freeze([
    Object.freeze([0.2104542553, 0.7936177850, -0.0040720468]),
    Object.freeze([1.9779984951, -2.4285922050, 0.4505937099]),
    Object.freeze([0.0259040371, 0.7827717662, -0.8086757660]),
]);
export const OKLAB_M1_INV = Object.freeze([
    Object.freeze([4.0767416621, -3.3077115913, 0.2309699292]),
    Object.freeze([-1.2684380046, 2.6097574011, -0.3413193965]),
    Object.freeze([-0.0041960863, -0.7034186147, 1.7076147010]),
]);
export const OKLAB_M2_INV = Object.freeze([
    Object.freeze([1.0, 0.3963377774, 0.2158037583]),
    Object.freeze([1.0, -0.1055613458, -0.0638541748]),
    Object.freeze([1.0, -0.0894841775, -1.2914855480]),
]);
export const OKLAB_C_NORM = 0.13;
export const BAND_NAMES = Object.freeze(['Red', 'Orange', 'Yellow', 'Green', 'Aqua', 'Blue', 'Purple', 'Magenta']);
export const BAND_CENTERS = Object.freeze([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 280.0, 320.0]);
export const HUE_SHIFT_DEGREES = 30.0;
export const HSL_LUMINANCE_FACTOR = 0.6;
export const NEUTRAL_PROTECT_START = 0.04;
export const NEUTRAL_PROTECT_END = 0.18;
export const GRAY_MIXER_FACTOR = 0.8;
export const VIBRANCE_FACTOR = 1.8;
export const CLARITY_FACTOR = 0.35;
export const TEXTURE_FACTOR = 0.30;
export const SHARPEN_FACTOR = 0.9;
export const SHARPEN_THRESHOLD = 0.004;
export const CLARITY_RESIDUAL_MAX = 0.25;
export const BLUR_LARGE_FACTOR = 0.02;
export const BLUR_SMALL_FACTOR = 0.004;
export const GAUSSIAN_TRUNCATE = 3.0;
export const VIGNETTE_FACTOR = 0.9;
export const VIGNETTE_MIDPOINT_MIN = 0.15;
export const VIGNETTE_MIDPOINT_RANGE = 0.85;
export const VIGNETTE_FEATHER_MIN = 0.02;
export const VIGNETTE_FEATHER_RANGE = 0.98;
export const VIGNETTE_ROUNDNESS_FACTOR = 0.5;
export const GRAIN_FACTOR = 0.12;
export const GRAIN_SEED = 0x9E3779B9;
export const GRAIN_X_MULTIPLIER = 374761393;
export const GRAIN_Y_MULTIPLIER = 668265263;
export const GRAIN_HASH_MULTIPLIER = 1274126177;
export const GRAIN_HASH_SHIFT = 13;
export const GRAIN_OUTPUT_SHIFT = 8;
export const GRAIN_OUTPUT_MASK = 0xFFFF;
export const GRAIN_OUTPUT_DIVISOR = 65535.0;
export const GRAIN_CELL_SIZE_MIN = 1.0;
export const GRAIN_CELL_SIZE_RANGE = 7.0;

export const PARITY_TABLE = Object.freeze({
    TINT_UV_SCALE, LUMA_RED, LUMA_GREEN, LUMA_BLUE, TONE_GAMMA,
    TONE_HIGHLIGHTS_FACTOR, TONE_SHADOWS_FACTOR, TONE_WHITES_FACTOR,
    TONE_BLACKS_FACTOR, TONE_EV_SIGMA, TONE_EV_HIGHLIGHTS_CENTER,
    TONE_EV_WHITES_CENTER, TONE_EV_SHADOWS_CENTER, TONE_EV_BLACKS_CENTER,
    TONE_HIGHLIGHTS_POS_SCALE, CONTRAST_FACTOR, SOFT_CLAMP_FACTOR, TONE_EPSILON,
    DEHAZE_AIRLIGHT_FACTOR, DEHAZE_SATURATION_FACTOR, SRGB_LINEAR_THRESHOLD,
    SRGB_ENCODE_SCALE, SRGB_ENCODE_A, SRGB_ENCODE_B, SRGB_ENCODE_GAMMA,
    SRGB_DECODE_THRESHOLD, SRGB_DECODE_SCALE, SRGB_DECODE_A, SRGB_DECODE_GAMMA,
    CURVE_LUT_SIZE, CURVE_MONOTONE_LIMIT, BASE_PROFILE_POINTS, BASE_PROFILE_SAT,
    OKLAB_M1, OKLAB_M2, OKLAB_M1_INV, OKLAB_M2_INV, OKLAB_C_NORM,
    BAND_NAMES, BAND_CENTERS, HUE_SHIFT_DEGREES, HSL_LUMINANCE_FACTOR,
    NEUTRAL_PROTECT_START, NEUTRAL_PROTECT_END, GRAY_MIXER_FACTOR, VIBRANCE_FACTOR,
    CLARITY_FACTOR, TEXTURE_FACTOR, SHARPEN_FACTOR, SHARPEN_THRESHOLD,
    CLARITY_RESIDUAL_MAX, BLUR_LARGE_FACTOR, BLUR_SMALL_FACTOR, GAUSSIAN_TRUNCATE,
    VIGNETTE_FACTOR, VIGNETTE_MIDPOINT_MIN, VIGNETTE_MIDPOINT_RANGE,
    VIGNETTE_FEATHER_MIN, VIGNETTE_FEATHER_RANGE, VIGNETTE_ROUNDNESS_FACTOR,
    GRAIN_FACTOR, GRAIN_SEED, GRAIN_X_MULTIPLIER, GRAIN_Y_MULTIPLIER,
    GRAIN_HASH_MULTIPLIER, GRAIN_HASH_SHIFT, GRAIN_OUTPUT_SHIFT, GRAIN_OUTPUT_MASK,
    GRAIN_OUTPUT_DIVISOR, GRAIN_CELL_SIZE_MIN, GRAIN_CELL_SIZE_RANGE,
});

export const DEFAULTS = Object.freeze({
    Temperature: 5500, Tint: 0, WhiteBalance: 'As Shot',
    Exposure2012: 0, Contrast2012: 0, Highlights2012: 0, Shadows2012: 0,
    Whites2012: 0, Blacks2012: 0, Texture: 0, Clarity2012: 0, Dehaze: 0,
    Vibrance: 0, Saturation: 0, ConvertToGrayscale: false,
    Sharpness: 40, SharpenRadius: 1, SharpenDetail: 25, SharpenEdgeMasking: 0,
    PostCropVignetteAmount: 0, PostCropVignetteMidpoint: 50,
    PostCropVignetteFeather: 50, PostCropVignetteRoundness: 0,
    GrainAmount: 0, GrainSize: 25, GrainFrequency: 50,
    CropLeft: 0, CropTop: 0, CropRight: 1, CropBottom: 1, CropAngle: 0, Orientation: 1,
});

export function numberSetting(settings, key, fallback = DEFAULTS[key] ?? 0) {
    const value = Number(settings?.[key]);
    return Number.isFinite(value) ? value : Number(fallback);
}

export function boolSetting(settings, key) {
    const value = settings?.[key];
    return value === true || value === 'True' || value === 'true' || value === 1;
}
