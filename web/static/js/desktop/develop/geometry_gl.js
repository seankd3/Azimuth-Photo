/** Post-develop geometry pass.
 *
 * Python export is the pixel-order contract: the full frame is developed first,
 * then orientation/crop/angle/Transform are applied.  Keeping this pass separate
 * prevents crop zoom from changing spatial operations such as NR and Clarity.
 */
export const GEOMETRY_FRAGMENT = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_developed;
uniform vec2 u_sourceSize;
uniform vec4 u_crop;
uniform float u_angle;
uniform int u_orientation;
uniform bool u_applyGeometry;
uniform mat3 u_transformInverse;

vec2 inverseOrientation(vec2 uv) {
    if (u_orientation == 3) return vec2(1.0) - uv;
    if (u_orientation == 6) return vec2(uv.y, 1.0 - uv.x);
    if (u_orientation == 8) return vec2(1.0 - uv.y, uv.x);
    return uv;
}

vec2 geometryUv(vec2 uv) {
    if (!u_applyGeometry) return uv;

    // Transform is Python's final geometry stage, so its inverse is sampled first.
    vec3 transformed = u_transformInverse * vec3((uv - .5) * 2.0, 1.0);
    if (abs(transformed.z) < 1e-6) return vec2(-1.0);
    uv = transformed.xy / transformed.z * .5 + .5;

    // Python rotates the discrete crop. The interactive canvas cannot expand to
    // the rotated export bounds without moving the workspace, but the inverse
    // sampling order and crop-relative aspect are the same.
    vec2 cropStartPx = floor(clamp(u_crop.xy, 0.0, 1.0) * u_sourceSize);
    vec2 cropEndPx = ceil(clamp(u_crop.zw, 0.0, 1.0) * u_sourceSize);
    vec2 cropPixels = max(cropEndPx - cropStartPx, vec2(1.0));
    vec2 q = uv - .5;
    float aspect = cropPixels.x / max(cropPixels.y, 1.0);
    q.x *= aspect;
    float angle = radians(u_angle);
    q = mat2(cos(angle), -sin(angle), sin(angle), cos(angle)) * q;
    q.x /= aspect;
    uv = (cropStartPx + (q + .5) * cropPixels) / u_sourceSize;
    return inverseOrientation(uv);
}

void main() {
    vec2 uv = geometryUv(v_uv);
    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) {
        outColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    outColor = texture(u_developed, uv);
}`;
