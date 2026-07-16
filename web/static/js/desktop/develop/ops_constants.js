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
// Scene-referred sigmoid view transform for HDR-merged bases (twin of ops_constants.py).
export const SIGMOID_FILM_POWER = 1.5;
export const SIGMOID_PAPER_POWER = 1.0;
export const SIGMOID_PAPER_EXP = 0.354355;
export const SIGMOID_FILM_FOG = 0.001426;
// Bounded S-curve contrast strength (max that provably never clips at |Contrast|=100).
export const CONTRAST_S_STRENGTH = 1.0;
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
export const DNG_LINEAR_SRGB_TO_PROPHOTO = [
    [0.5293232202529907, 0.3300613760948181, 0.14062660932540894],
    [0.09840947389602661, 0.8734501600265503, 0.028135817497968674],
    [0.016889531165361404, 0.1177167221903801, 0.8657695651054382],
];
export const DNG_PROPHOTO_TO_LINEAR_SRGB = [
    [2.034219264984131, -0.7273500561714172, -0.30677998065948486],
    [-0.2289147973060608, 1.23177170753479, -0.0028476316947489977],
    [-0.008558756671845913, -0.1532919704914093, 1.1614136695861816],
];
export const DNG_TONE_LUT_SIZE = 1025;
export const DNG_TABLE_CHANNELS = 4;
export const CAMERA_PROFILE_TONE_NODES = 16;
export const CAMERA_PROFILE_HUE_BINS = 12;
export const CAMERA_PROFILE_CHROMA_BINS = 3;
export const CAMERA_PROFILE_BIN_CENTER = 0.5;
export const CAMERA_PROFILE_PI = 3.141592653589793;
export const CAMERA_PROFILE_TWO_PI = 6.283185307179586;
export const LENS_NORMALIZED_HALF_MIN = 2.0;
export const LENS_IMAGE_CENTER = 0.5;
export const LENS_VIGNETTE_GAIN_MIN = 0.0;
export const LENS_VIGNETTE_GAIN_MAX = 8.0;
export const LENS_AUTO_CROP_EDGE_SAMPLES = 32;
// Lens Corrections panel scales are percentage multipliers: 100 preserves the
// Lensfun profile, 0 is identity, and 200 doubles the profile delta.
export const LENS_PROFILE_SCALE_MIN = 0.0;
export const LENS_PROFILE_SCALE_MAX = 200.0;
export const LENS_PROFILE_SCALE_DEFAULT = 100.0;
export const LENS_MANUAL_DISTORTION_FACTOR = 0.25;
export const LENS_MANUAL_VIGNETTE_FACTOR = 0.75;
export const LENS_MANUAL_VIGNETTE_MIDPOINT_MIN = 0.15;
export const LENS_MANUAL_VIGNETTE_MIDPOINT_RANGE = 0.85;

// Calibration (§28) is a linear-RGB 3x3 primary-column perturbation. A hue
// control moves only its matching input-primary column; saturation expands or
// contracts that column around luma. Shadow Tint is a restrained
// green↔magenta multiplier under the shadow luma cutoff.
export const CALIBRATION_HUE_MIX = 0.18;
export const CALIBRATION_SATURATION_SCALE = 0.50;
export const CALIBRATION_SHADOW_TINT_SCALE = 0.12;
export const CALIBRATION_SHADOW_START = 0.02;
export const CALIBRATION_SHADOW_END = 0.30;
export const PERSPECTIVE_AMOUNT_SCALE = 0.0035;
export const PERSPECTIVE_ASPECT_SCALE = 0.01;
export const PERSPECTIVE_OFFSET_SCALE = 0.01;
export const PERSPECTIVE_SCALE_BASE = 100.0;
export const PERSPECTIVE_SCALE_MIN = 1.0;
export const HORIZON_ANGLE_LIMIT = 20.0;
export const HORIZON_THETA_STEPS = 81;
export const HORIZON_EDGE_PERCENTILE = 92.0;
export const HORIZON_MAX_POINTS = 12000;
export const COLOR_GRADE_AB_SCALE = 0.12;
export const COLOR_GRADE_LUMINANCE_EV = 1.0;
export const COLOR_GRADE_SHADOW_CENTER = 0.35;
export const COLOR_GRADE_HIGHLIGHT_CENTER = 0.65;
export const COLOR_GRADE_BLEND_MIN = 0.12;
export const COLOR_GRADE_BLEND_RANGE = 0.28;
export const COLOR_GRADE_BALANCE_SHIFT = 0.18;
export const NR_LUMA_SIGMA = 0.08;
export const NR_LUMA_SPATIAL_CENTER = 4.0;
export const NR_LUMA_SPATIAL_AXIS = 2.0;
export const NR_COLOR_RADIUS = 1;
export const NR_DETAIL_EDGE_LOW = 0.004;
export const NR_DETAIL_EDGE_HIGH = 0.04;
export const NR_CONTRAST_RESIDUAL = 0.5;
export const DEFRINGE_HUE_SCALE = 3.6;
export const DEFRINGE_EDGE_LOW = 0.004;
export const DEFRINGE_EDGE_HIGH = 0.04;
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
export const SHARPEN_MASK_EDGE_LOW = 0.004;
export const SHARPEN_MASK_EDGE_HIGH = 0.04;
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
export const GRAIN_FREQUENCY_MIN = 0.5;
export const GRAIN_FREQUENCY_RANGE = 1.0;
export const SOFT_PROOF_SRGB_TO_XYZ = Object.freeze([
    Object.freeze([0.4124564, 0.3575761, 0.1804375]), Object.freeze([0.2126729, 0.7151522, 0.0721750]), Object.freeze([0.0193339, 0.1191920, 0.9503041]),
]);
export const SOFT_PROOF_XYZ_TO_SRGB = Object.freeze([
    Object.freeze([3.2404542, -1.5371385, -0.4985314]), Object.freeze([-0.9692660, 1.8760108, 0.0415560]), Object.freeze([0.0556434, -0.2040259, 1.0572252]),
]);
export const SOFT_PROOF_ADOBE_RGB_TO_XYZ = Object.freeze([
    Object.freeze([0.5767309, 0.1855540, 0.1881852]), Object.freeze([0.2973769, 0.6273491, 0.0752741]), Object.freeze([0.0270343, 0.0706872, 0.9911085]),
]);
export const SOFT_PROOF_XYZ_TO_ADOBE_RGB = Object.freeze([
    Object.freeze([2.0413690, -0.5649464, -0.3446944]), Object.freeze([-0.9692660, 1.8760108, 0.0415560]), Object.freeze([0.0134474, -0.1183897, 1.0154096]),
]);
export const SOFT_PROOF_P3_TO_XYZ = Object.freeze([
    Object.freeze([0.4865709, 0.2656676, 0.1982173]), Object.freeze([0.2289746, 0.6917385, 0.0792869]), Object.freeze([0.0000000, 0.0451134, 1.0439444]),
]);
export const SOFT_PROOF_XYZ_TO_P3 = Object.freeze([
    Object.freeze([2.4934969, -0.9313836, -0.4027108]), Object.freeze([-0.8294890, 1.7626641, 0.0236247]), Object.freeze([0.0358458, -0.0761724, 0.9568845]),
]);
export const SOFT_PROOF_PAPER_WHITE = 0.92;
export const SOFT_PROOF_PAPER_BLACK = 0.02;
// Adobe-native local fractions. Catalog evidence and the EV choice are
// documented beside the exact Python twins in features/develop/ops_constants.py.
export const LOCAL_RENDER_CAP = 16;
export const LOCAL_MASK_DOWNSAMPLE = 4;
export const LOCAL_MASK_ATLAS_COLUMNS = 4;
export const LOCAL_EXPOSURE_EV_SCALE = 4.0;
export const LOCAL_SLIDER_SCALE = 100.0;
export const LOCAL_WB_MIRED_SCALE = 30.0;
export const LOCAL_WB_TEMP_FACTOR = 0.0007;
export const LOCAL_WB_TINT_FACTOR = 0.0035;
export const LOCAL_HUE_DEGREES = 180.0;
export const LOCAL_BRUSH_DEFAULT_RADIUS = 0.05;
export const LOCAL_BRUSH_GAUSSIAN_SIGMA = 1.0 / 3.0;
export const LOCAL_COLOR_SIGMA_MIN = 0.015;
export const LOCAL_COLOR_SIGMA_RANGE = 0.18;
export const LOCAL_RANGE_EPSILON = 1e-6;
export const RETOUCH_RENDER_CAP = 32;
export const RETOUCH_RING_TAPS = 8;
export const RETOUCH_RING_SCALE = 1.5;
export const RETOUCH_MIN_RADIUS = 1e-4;

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
    DNG_LINEAR_SRGB_TO_PROPHOTO, DNG_PROPHOTO_TO_LINEAR_SRGB, DNG_TONE_LUT_SIZE, DNG_TABLE_CHANNELS,
    CAMERA_PROFILE_TONE_NODES, CAMERA_PROFILE_HUE_BINS, CAMERA_PROFILE_CHROMA_BINS,
    CAMERA_PROFILE_BIN_CENTER, CAMERA_PROFILE_PI, CAMERA_PROFILE_TWO_PI,
    LENS_NORMALIZED_HALF_MIN, LENS_IMAGE_CENTER, LENS_VIGNETTE_GAIN_MIN, LENS_VIGNETTE_GAIN_MAX, LENS_AUTO_CROP_EDGE_SAMPLES,
    LENS_PROFILE_SCALE_MIN, LENS_PROFILE_SCALE_MAX, LENS_PROFILE_SCALE_DEFAULT,
    LENS_MANUAL_DISTORTION_FACTOR, LENS_MANUAL_VIGNETTE_FACTOR,
    LENS_MANUAL_VIGNETTE_MIDPOINT_MIN, LENS_MANUAL_VIGNETTE_MIDPOINT_RANGE,
    CALIBRATION_HUE_MIX, CALIBRATION_SATURATION_SCALE, CALIBRATION_SHADOW_TINT_SCALE,
    CALIBRATION_SHADOW_START, CALIBRATION_SHADOW_END,
    PERSPECTIVE_AMOUNT_SCALE, PERSPECTIVE_ASPECT_SCALE, PERSPECTIVE_OFFSET_SCALE,
    PERSPECTIVE_SCALE_BASE, PERSPECTIVE_SCALE_MIN, HORIZON_ANGLE_LIMIT,
    HORIZON_THETA_STEPS, HORIZON_EDGE_PERCENTILE, HORIZON_MAX_POINTS,
    COLOR_GRADE_AB_SCALE, COLOR_GRADE_LUMINANCE_EV, COLOR_GRADE_SHADOW_CENTER,
    COLOR_GRADE_HIGHLIGHT_CENTER, COLOR_GRADE_BLEND_MIN, COLOR_GRADE_BLEND_RANGE,
    COLOR_GRADE_BALANCE_SHIFT, NR_LUMA_SIGMA, NR_LUMA_SPATIAL_CENTER, NR_LUMA_SPATIAL_AXIS,
    NR_COLOR_RADIUS, DEFRINGE_HUE_SCALE, DEFRINGE_EDGE_LOW, DEFRINGE_EDGE_HIGH,
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
    GRAIN_FREQUENCY_MIN, GRAIN_FREQUENCY_RANGE,
    LOCAL_RENDER_CAP, LOCAL_MASK_DOWNSAMPLE, LOCAL_MASK_ATLAS_COLUMNS,
    LOCAL_EXPOSURE_EV_SCALE, LOCAL_SLIDER_SCALE, LOCAL_WB_MIRED_SCALE,
    LOCAL_WB_TEMP_FACTOR, LOCAL_WB_TINT_FACTOR, LOCAL_HUE_DEGREES,
    LOCAL_BRUSH_DEFAULT_RADIUS, LOCAL_BRUSH_GAUSSIAN_SIGMA,
    LOCAL_COLOR_SIGMA_MIN, LOCAL_COLOR_SIGMA_RANGE, LOCAL_RANGE_EPSILON,
    RETOUCH_RENDER_CAP, RETOUCH_RING_TAPS, RETOUCH_RING_SCALE, RETOUCH_MIN_RADIUS,
});

export const DEFAULTS = Object.freeze({
    Temperature: 5500, Tint: 0, WhiteBalance: 'As Shot',
    Exposure2012: 0, Contrast2012: 0, Highlights2012: 0, Shadows2012: 0,
    Whites2012: 0, Blacks2012: 0, Texture: 0, Clarity2012: 0, Dehaze: 0,
    Vibrance: 0, Saturation: 0, ConvertToGrayscale: false,
    Sharpness: 0, SharpenRadius: 1, SharpenEdgeMasking: 0,
    LuminanceSmoothing: 0, LuminanceDetail: 50, LuminanceContrast: 0, ColorNoiseReduction: 0,
    DefringePurpleAmount: 0, DefringePurpleHueLo: 30, DefringePurpleHueHi: 70,
    DefringeGreenAmount: 0, DefringeGreenHueLo: 40, DefringeGreenHueHi: 60,
    PostCropVignetteAmount: 0, PostCropVignetteMidpoint: 50,
    PostCropVignetteFeather: 50, PostCropVignetteRoundness: 0,
    GrainAmount: 0, GrainSize: 25, GrainFrequency: 50,
    CropLeft: 0, CropTop: 0, CropRight: 1, CropBottom: 1, CropAngle: 0, Orientation: 1,
    PerspectiveVertical: 0, PerspectiveHorizontal: 0, PerspectiveRotate: 0,
    PerspectiveScale: 100, PerspectiveAspect: 0, PerspectiveX: 0, PerspectiveY: 0,
    PerspectiveUpright: 'Off',
    LensProfileEnable: false, LensProfileDistortionScale: 100, LensProfileVignettingScale: 100,
    LensManualDistortionAmount: 0, LensManualVignetteAmount: 0, LensManualVignetteMidpoint: 50,
    CalibrationShadowTint: 0,
    CalibrationRedPrimaryHue: 0, CalibrationRedPrimarySaturation: 0,
    CalibrationGreenPrimaryHue: 0, CalibrationGreenPrimarySaturation: 0,
    CalibrationBluePrimaryHue: 0, CalibrationBluePrimarySaturation: 0,
});

export function numberSetting(settings, key, fallback = DEFAULTS[key] ?? 0) {
    const value = Number(settings?.[key]);
    return Number.isFinite(value) ? value : Number(fallback);
}

export function boolSetting(settings, key) {
    const value = settings?.[key];
    return value === true || value === 'True' || value === 'true' || value === 1;
}
