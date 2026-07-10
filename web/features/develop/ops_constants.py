"""Shared develop-operation constants.

Keep this module data-only: ``PARITY_TABLE`` is the source mirrored by the
desktop renderer. Camera-profile, lens, and local-adjustment math consume these
same named values in the NumPy and WebGL twins.
"""


# As-shot tint: signed Δv' from the Planckian locus × this scale → LR-ish −150..150.
TINT_UV_SCALE = 3000.0

LUMA_RED = 0.2126
LUMA_GREEN = 0.7152
LUMA_BLUE = 0.0722
TONE_GAMMA = 2.2

# Log-EV region tone (toneequal-style overlapping Gaussians). Factors are EV at
# slider ±100 when the Gaussian weight is 1.0. Highlights −100 recovers ~2 EV.
TONE_HIGHLIGHTS_FACTOR = 2.0
TONE_SHADOWS_FACTOR = 1.5
TONE_WHITES_FACTOR = 1.0
TONE_BLACKS_FACTOR = 1.0
TONE_EV_SIGMA = 1.8
TONE_EV_HIGHLIGHTS_CENTER = 1.5
TONE_EV_WHITES_CENTER = 3.0
TONE_EV_SHADOWS_CENTER = -3.5
TONE_EV_BLACKS_CENTER = -6.0
# Positive Highlights are gentler than recovery so whites do not blow out.
TONE_HIGHLIGHTS_POS_SCALE = 0.55

CONTRAST_FACTOR = 0.85
SOFT_CLAMP_FACTOR = 4.0
TONE_EPSILON = 1e-6

DEHAZE_AIRLIGHT_FACTOR = 0.12
DEHAZE_SATURATION_FACTOR = 0.15

SRGB_LINEAR_THRESHOLD = 0.0031308
SRGB_ENCODE_SCALE = 12.92
SRGB_ENCODE_A = 1.055
SRGB_ENCODE_B = 0.055
SRGB_ENCODE_GAMMA = 1.0 / 2.4
SRGB_DECODE_THRESHOLD = 0.04045
SRGB_DECODE_SCALE = 1.0 / 12.92
SRGB_DECODE_A = 0.055
SRGB_DECODE_GAMMA = 2.4

CURVE_LUT_SIZE = 256
CURVE_MONOTONE_LIMIT = 3.0

# Fixed Adobe-Color-ish base profile (0..255 control points) + mild chroma lift.
BASE_PROFILE_POINTS = (
    (0.0, 0.0),
    (20.0, 20.0),
    (40.0, 39.0),
    (64.0, 81.0),
    (96.0, 156.0),
    (128.0, 205.0),
    (176.0, 232.0),
    (216.0, 245.0),
    (255.0, 255.0),
)
BASE_PROFILE_SAT = 1.22

# Fitted camera profiles: 12 circular OKLab hue bins x 3 chroma bins.
CAMERA_PROFILE_TONE_NODES = 16
CAMERA_PROFILE_HUE_BINS = 12
CAMERA_PROFILE_CHROMA_BINS = 3
CAMERA_PROFILE_BIN_CENTER = 0.5
CAMERA_PROFILE_PI = 3.141592653589793
CAMERA_PROFILE_TWO_PI = 6.283185307179586

# Lensfun radial models normalize radius against half the image's short edge.
LENS_NORMALIZED_HALF_MIN = 2.0
LENS_IMAGE_CENTER = 0.5
LENS_VIGNETTE_GAIN_MIN = 0.0
LENS_VIGNETTE_GAIN_MAX = 8.0
# Distortion auto-crop evaluates radial scale along every frame edge. The
# returned UV scale is <= 1: identity is 1, lower values zoom to remove borders.
LENS_AUTO_CROP_EDGE_SAMPLES = 32

# Transform/Upright (§28): normalized centred-UV homography coefficients.
PERSPECTIVE_AMOUNT_SCALE = 0.0035
PERSPECTIVE_ASPECT_SCALE = 0.01
PERSPECTIVE_OFFSET_SCALE = 0.01
PERSPECTIVE_SCALE_BASE = 100.0
PERSPECTIVE_SCALE_MIN = 1.0
HORIZON_ANGLE_LIMIT = 20.0
HORIZON_THETA_STEPS = 81
HORIZON_EDGE_PERCENTILE = 92.0
HORIZON_MAX_POINTS = 12000

# §22 order: HSL/vibrance -> grade -> defringe -> NR -> local -> detail.
# Grade works in OKLab-ish ab; wheel luminance is a band exposure adjustment.
COLOR_GRADE_AB_SCALE = 0.12
COLOR_GRADE_LUMINANCE_EV = 1.0
COLOR_GRADE_SHADOW_CENTER = 0.35
COLOR_GRADE_HIGHLIGHT_CENTER = 0.65
COLOR_GRADE_BLEND_MIN = 0.12
COLOR_GRADE_BLEND_RANGE = 0.28
COLOR_GRADE_BALANCE_SHIFT = 0.18

# Conservative bilateral-lite luma NR at half res, then OKLab-ab smoothing.
NR_LUMA_SIGMA = 0.08
NR_LUMA_SPATIAL_CENTER = 4.0
NR_LUMA_SPATIAL_AXIS = 2.0
NR_COLOR_RADIUS = 1

# Lightroom Defringe hue endpoints are 0..100, mapped to 0..360 degrees.
# AutoLateralCA remains a later lane; only manual defringe renders here.
DEFRINGE_HUE_SCALE = 3.6
DEFRINGE_EDGE_LOW = 0.004
DEFRINGE_EDGE_HIGH = 0.04

# OKLab (Ottosson): linear sRGB → LMS → OKLab. Nested rows, row-major.
OKLAB_M1 = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)
OKLAB_M2 = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)
OKLAB_M1_INV = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)
OKLAB_M2_INV = (
    (1.0, 0.3963377774, 0.2158037583),
    (1.0, -0.1055613458, -0.0638541748),
    (1.0, -0.0894841775, -1.2914855480),
)
# Typical max OKLab C for in-gamut sRGB; vibrance sat-ness = C / this.
OKLAB_C_NORM = 0.13

BAND_NAMES = ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")
BAND_CENTERS = (0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 280.0, 320.0)
HUE_SHIFT_DEGREES = 30.0
HSL_LUMINANCE_FACTOR = 0.6
NEUTRAL_PROTECT_START = 0.04
NEUTRAL_PROTECT_END = 0.18
GRAY_MIXER_FACTOR = 0.8

VIBRANCE_FACTOR = 1.8
CLARITY_FACTOR = 0.35
TEXTURE_FACTOR = 0.30
SHARPEN_FACTOR = 0.9
SHARPEN_THRESHOLD = 0.004
CLARITY_RESIDUAL_MAX = 0.25
BLUR_LARGE_FACTOR = 0.02
BLUR_SMALL_FACTOR = 0.004
GAUSSIAN_TRUNCATE = 3.0

VIGNETTE_FACTOR = 0.9
VIGNETTE_MIDPOINT_MIN = 0.15
VIGNETTE_MIDPOINT_RANGE = 0.85
VIGNETTE_FEATHER_MIN = 0.02
VIGNETTE_FEATHER_RANGE = 0.98
VIGNETTE_ROUNDNESS_FACTOR = 0.5

GRAIN_FACTOR = 0.12
GRAIN_SEED = 0x9E3779B9
GRAIN_X_MULTIPLIER = 374761393
GRAIN_Y_MULTIPLIER = 668265263
GRAIN_HASH_MULTIPLIER = 1274126177
GRAIN_HASH_SHIFT = 13
GRAIN_OUTPUT_SHIFT = 8
GRAIN_OUTPUT_MASK = 0xFFFF
GRAIN_OUTPUT_DIVISOR = 65535.0
GRAIN_CELL_SIZE_MIN = 1.0
GRAIN_CELL_SIZE_RANGE = 7.0

# Local corrections (§12).  A 2023-v13.lrcat exposure-only brush on
# 20230111-R5__9359 stores LocalExposure2012=-0.4835; Lightroom history calls
# that step "Update Exposure Adjustment". Mapping Adobe's stored fraction by
# ×4 gives a plausible -1.934 EV, while observed exact ±1 values map to the
# documented ±4 EV endpoints. Keep Adobe-native fractions in settings JSON.
LOCAL_RENDER_CAP = 16
LOCAL_MASK_DOWNSAMPLE = 4
LOCAL_MASK_ATLAS_COLUMNS = 4
LOCAL_EXPOSURE_EV_SCALE = 4.0
LOCAL_SLIDER_SCALE = 100.0
LOCAL_WB_MIRED_SCALE = 30.0
LOCAL_WB_TEMP_FACTOR = 0.0007
LOCAL_WB_TINT_FACTOR = 0.0035
LOCAL_HUE_DEGREES = 180.0
LOCAL_BRUSH_DEFAULT_RADIUS = 0.05
LOCAL_BRUSH_GAUSSIAN_SIGMA = 1.0 / 3.0
LOCAL_COLOR_SIGMA_MIN = 0.015
LOCAL_COLOR_SIGMA_RANGE = 0.18
LOCAL_RANGE_EPSILON = 1e-6

# Circular clone/heal spots (§23), rendered as the final pixel operation.
RETOUCH_RENDER_CAP = 32
RETOUCH_RING_TAPS = 8
RETOUCH_RING_SCALE = 1.5
RETOUCH_MIN_RADIUS = 1e-4

PARITY_TABLE = {
    name: value
    for name, value in tuple(globals().items())
    if name.isupper() and name != "PARITY_TABLE"
}

# ---------------------------------------------------------------------------
# Export-only output sharpening (§24). Applied AFTER geometry/resize in the
# Python export path as a classic unsharp mask on gamma luma. There is NO
# WebGL twin — these constants intentionally stay out of PARITY_TABLE and
# ops_constants.js.
# Amounts are residual multipliers; radii are Gaussian σ in pixels at the
# export pixel size (screen = fine, print = coarser for ink spread).
# ---------------------------------------------------------------------------
OUTPUT_SHARPEN_SCREEN_LOW_AMOUNT = 0.35
OUTPUT_SHARPEN_SCREEN_LOW_RADIUS = 0.5
OUTPUT_SHARPEN_SCREEN_STANDARD_AMOUNT = 0.55
OUTPUT_SHARPEN_SCREEN_STANDARD_RADIUS = 0.6
OUTPUT_SHARPEN_SCREEN_HIGH_AMOUNT = 0.85
OUTPUT_SHARPEN_SCREEN_HIGH_RADIUS = 0.7
OUTPUT_SHARPEN_PRINT_LOW_AMOUNT = 0.45
OUTPUT_SHARPEN_PRINT_LOW_RADIUS = 1.0
OUTPUT_SHARPEN_PRINT_STANDARD_AMOUNT = 0.75
OUTPUT_SHARPEN_PRINT_STANDARD_RADIUS = 1.2
OUTPUT_SHARPEN_PRINT_HIGH_AMOUNT = 1.1
OUTPUT_SHARPEN_PRINT_HIGH_RADIUS = 1.4

OUTPUT_SHARPEN_PRESETS = {
    "none": (0.0, 0.0),
    "screen_low": (OUTPUT_SHARPEN_SCREEN_LOW_AMOUNT, OUTPUT_SHARPEN_SCREEN_LOW_RADIUS),
    "screen_standard": (OUTPUT_SHARPEN_SCREEN_STANDARD_AMOUNT, OUTPUT_SHARPEN_SCREEN_STANDARD_RADIUS),
    "screen_high": (OUTPUT_SHARPEN_SCREEN_HIGH_AMOUNT, OUTPUT_SHARPEN_SCREEN_HIGH_RADIUS),
    "print_low": (OUTPUT_SHARPEN_PRINT_LOW_AMOUNT, OUTPUT_SHARPEN_PRINT_LOW_RADIUS),
    "print_standard": (OUTPUT_SHARPEN_PRINT_STANDARD_AMOUNT, OUTPUT_SHARPEN_PRINT_STANDARD_RADIUS),
    "print_high": (OUTPUT_SHARPEN_PRINT_HIGH_AMOUNT, OUTPUT_SHARPEN_PRINT_HIGH_RADIUS),
}
