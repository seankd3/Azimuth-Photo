"""Static contracts for the progressive Develop canvas state machine.

The frontend has no standalone Node test runner in this repository.  These
small source-level contracts keep the two intentionally ordered paints honest:
cached Develop JPEG first, cached Library lg thumbnail only on a cold base,
then the display-bypassed GL preview swaps to the linear renderer.
"""

from pathlib import Path


WEB = Path(__file__).parent


def test_develop_progressive_loader_prefers_cached_base_then_library_fallback():
    source = (WEB / "static/js/desktop/develop/develop.js").read_text(encoding="utf-8")
    assert "fetch(`/api/develop/${imageId}/base.jpg`, fetchOptionsWithTimeout" in source
    assert "thumbUrl('lg', imageId)}?cached=1" in source
    assert source.index("base.jpg") < source.index("thumbUrl('lg', imageId)}?cached=1")
    assert "DEVELOP_BASE_BUDGET_MS = 12_000" in source
    assert "throw new PendingOriginalError()" in source
    assert "Original is still on the hub — retrying in background" in source
    assert "root.dataset.developOpenMs" in source
    assert "setControlsLoading(true)" in source
    assert "histogram?.setLoading(true)" in source


def test_gl_preview_is_display_referred_and_swaps_to_linear_source():
    source = (WEB / "static/js/desktop/develop/gl.js").read_text(encoding="utf-8")
    assert "const DISPLAY_PREVIEW_FRAGMENT" in source
    assert "uniform int u_orientation;" in source
    assert "if (u_orientation == 3) return vec2(1.0) - uv;" in source
    assert "if (u_orientation == 6) return vec2(uv.y, 1.0 - uv.x);" in source
    assert "if (u_orientation == 8) return vec2(1.0 - uv.y, uv.x);" in source
    assert "uploadDisplayPreview(image, orientation = 1)" in source
    loader = (WEB / "static/js/desktop/develop/develop.js").read_text(encoding="utf-8")
    assert "paintPlaceholder(image.id, token, displayOrientation(entry))" in loader
    assert "orientation: Number(payload.orientation) || 1" in loader
    assert "X-Develop-Orientation" not in loader
    assert "this.displayPreview = true" in source
    assert "this.displayPreview = false" in source
    assert "gl.useProgram(this.displayPreviewProgram)" in source
