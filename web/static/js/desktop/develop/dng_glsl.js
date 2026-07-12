import { DNG_TABLE_CHANNELS, DNG_TONE_LUT_SIZE } from './ops_constants.js';

const ILLUMINANT_CCT = Object.freeze({ 17: 2856, 18: 4874, 19: 6774, 20: 5503, 21: 6504, 22: 7504, 23: 5003 });

function illuminantTemperature(value, fallback) {
    const number = Number(value);
    if (ILLUMINANT_CCT[number]) return ILLUMINANT_CCT[number];
    return Number.isFinite(number) && number > 1000 ? number : fallback;
}

export function dualIlluminantWeight(cct, illuminant1 = 17, illuminant2 = 21) {
    const first = illuminantTemperature(illuminant1, 2856);
    const second = illuminantTemperature(illuminant2, 6504);
    if (!(first > 0) || !(second > 0) || first === second) return 1;
    const reverse = first > second;
    const low = reverse ? second : first;
    const high = reverse ? first : second;
    const temperature = Math.max(Number(cct) || 5500, 1);
    let weight;
    if (temperature <= low) weight = 1;
    else if (temperature >= high) weight = 0;
    else weight = ((1 / temperature) - (1 / high)) / ((1 / low) - (1 / high));
    return reverse ? 1 - weight : weight;
}

export function packDngTable(table, profile, cct) {
    const dims = Array.isArray(table?.dims) ? table.dims.map(Number) : [0, 0, 0];
    const [hue, saturation, value] = dims;
    const count = hue * saturation * value;
    if (!(hue >= 1 && saturation >= 2 && value >= 1 && count > 0)) return null;
    const first = table.data1 || table.data;
    if (!Array.isArray(first) || first.length !== count * 3) return null;
    const second = Array.isArray(table.data2) && table.data2.length === count * 3 ? table.data2 : null;
    const weight = second ? dualIlluminantWeight(
        cct, profile?.calibration_illuminant1, profile?.calibration_illuminant2,
    ) : 1;
    const data = new Float32Array(count * DNG_TABLE_CHANNELS);
    for (let index = 0; index < count; index += 1) {
        for (let channel = 0; channel < 3; channel += 1) {
            const offset = index * 3 + channel;
            data[index * 4 + channel] = second
                ? weight * Number(first[offset]) + (1 - weight) * Number(second[offset])
                : Number(first[offset]);
        }
        data[index * 4 + 3] = 1;
    }
    // DNG file order is value-major -> hue -> saturation. A valDivisions=1
    // table is a plain 2D hue×sat texture; otherwise value slices are packed
    // vertically. Manual texelFetch interpolation in GLSL mirrors NumPy.
    return { data, width: saturation, height: hue * value, dims: [hue, saturation, value] };
}

export function packDngTone(profile) {
    const values = profile?.tone_curve_lut;
    if (!Array.isArray(values) || values.length !== DNG_TONE_LUT_SIZE) return null;
    const data = new Float32Array(DNG_TONE_LUT_SIZE * 4);
    for (let index = 0; index < DNG_TONE_LUT_SIZE; index += 1) {
        const value = Number(values[index]);
        data.set([value, value, value, 1], index * 4);
    }
    return data;
}

export const DNG_GLSL = `
vec3 dngRgbToHsv6(vec3 source) {
    vec3 rgb = clamp(source, 0.0, 1.0);
    float value = max(rgb.r, max(rgb.g, rgb.b));
    float minimum = min(rgb.r, min(rgb.g, rgb.b));
    float gap = value - minimum;
    if (gap <= 0.0) return vec3(0.0, 0.0, value);
    float hue;
    if (rgb.r == value) hue = mod((rgb.g - rgb.b) / gap, 6.0);
    else if (rgb.g == value) hue = 2.0 + (rgb.b - rgb.r) / gap;
    else hue = 4.0 + (rgb.r - rgb.g) / gap;
    return vec3(hue, gap / max(value, 1e-20), value);
}
vec3 dngHsv6ToRgb(vec3 hsv) {
    float h = mod(hsv.x, 6.0);
    float f = fract(h);
    float p = hsv.z * (1.0 - hsv.y);
    float q = hsv.z * (1.0 - hsv.y * f);
    float t = hsv.z * (1.0 - hsv.y * (1.0 - f));
    int sector = int(floor(h));
    if (sector == 0) return vec3(hsv.z, t, p);
    if (sector == 1) return vec3(q, hsv.z, p);
    if (sector == 2) return vec3(p, hsv.z, t);
    if (sector == 3) return vec3(p, q, hsv.z);
    if (sector == 4) return vec3(t, p, hsv.z);
    return vec3(hsv.z, p, q);
}
vec3 dngTableFetch(sampler2D table, ivec3 dims, int h, int s, int v) {
    return texelFetch(table, ivec2(s, v * dims.x + h), 0).rgb;
}
vec3 dngApplyTable(vec3 rgb, sampler2D table, ivec3 dims, int encoding) {
    if (dims.x < 1 || dims.y < 2 || dims.z < 1) return rgb;
    vec3 hsv = dngRgbToHsv6(rgb);
    float encodedValue = encoding != 0 ? linearToSrgb(vec3(hsv.z)).r : hsv.z;
    float hp = hsv.x * float(dims.x) / 6.0;
    float sp = hsv.y * float(dims.y - 1);
    float vp = encodedValue * float(dims.z - 1);
    int hRaw = int(floor(hp));
    int h0 = int(mod(float(hRaw), float(dims.x)));
    int h1 = (h0 + 1) % dims.x;
    int s0 = clamp(int(floor(sp)), 0, dims.y - 2);
    int s1 = s0 + 1;
    int v0 = dims.z > 1 ? clamp(int(floor(vp)), 0, dims.z - 2) : 0;
    int v1 = dims.z > 1 ? v0 + 1 : 0;
    float hf = hp - float(hRaw);
    float sf = sp - float(s0);
    float vf = dims.z > 1 ? vp - float(v0) : 0.0;
    vec3 a0 = mix(dngTableFetch(table, dims, h0, s0, v0), dngTableFetch(table, dims, h0, s1, v0), sf);
    vec3 a1 = mix(dngTableFetch(table, dims, h1, s0, v0), dngTableFetch(table, dims, h1, s1, v0), sf);
    vec3 delta0 = mix(a0, a1, hf);
    vec3 delta = delta0;
    if (dims.z > 1) {
        vec3 b0 = mix(dngTableFetch(table, dims, h0, s0, v1), dngTableFetch(table, dims, h0, s1, v1), sf);
        vec3 b1 = mix(dngTableFetch(table, dims, h1, s0, v1), dngTableFetch(table, dims, h1, s1, v1), sf);
        delta = mix(delta0, mix(b0, b1, hf), vf);
    }
    hsv.x += delta.x * (6.0 / 360.0);
    hsv.y = min(hsv.y * delta.y, 1.0);
    encodedValue = clamp(encodedValue * delta.z, 0.0, 1.0);
    hsv.z = encoding != 0 ? srgbToLinear(vec3(encodedValue)).r : encodedValue;
    return dngHsv6ToRgb(hsv);
}
float dngToneSample(float value) {
    float position = clamp(value, 0.0, 1.0) * float(${DNG_TONE_LUT_SIZE - 1});
    int low = int(floor(position));
    int high = min(low + 1, ${DNG_TONE_LUT_SIZE - 1});
    return mix(texelFetch(u_dngTone, ivec2(low, 0), 0).r,
        texelFetch(u_dngTone, ivec2(high, 0), 0).r, position - float(low));
}
vec3 dngApplyTone(vec3 source) {
    vec3 rgb = clamp(source, 0.0, 1.0);
    float minimum = min(rgb.r, min(rgb.g, rgb.b));
    float maximum = max(rgb.r, max(rgb.g, rgb.b));
    float curvedMin = dngToneSample(minimum);
    float curvedMax = dngToneSample(maximum);
    float span = maximum - minimum;
    if (span <= 0.0) return vec3(curvedMin);
    return vec3(curvedMin) + (curvedMax - curvedMin) * ((rgb - vec3(minimum)) / span);
}
`;
