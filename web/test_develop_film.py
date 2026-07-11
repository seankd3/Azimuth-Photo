import os
import unittest

import numpy as np

os.environ.setdefault("PHOTOARCHIVE_SMOKE_MODE", "1")

from features.develop import film

# Stocks added 2026-07-11 (library expansion). fuji-velvia-50 is the first
# positive/slide stock and exercises the non-negative print path.
NEW_STOCKS = (
    "portra-800",
    "fuji-velvia-50",
    "kodak-ultramax-400",
    "kodak-colorplus-200",
    "fujicolor-c200",
    "kodak-tmax-400",
)


class DevelopFilmTests(unittest.TestCase):
    def test_stocks_load_and_tables_build(self):
        stocks = film.list_stocks()
        self.assertGreaterEqual(len(stocks), 14)
        for entry in stocks:
            tables = film.build_film_tables(film.load_stock(entry["slug"]))
            self.assertEqual(tables["hd_lut"].shape, (film.FILM_LUT_SIZE, 3))
            self.assertEqual(tables["crosstalk"].shape, (3, 3))
            self.assertEqual(tables["print_lut"].shape, (film.FILM_LUT_SIZE,))

    def test_mid_gray_prints_near_middle_gray(self):
        mid = np.full((16, 16, 3), film.FILM_MID_GRAY, dtype=np.float32)
        for slug in ("portra-400", "cinestill-800t", "kodak-tri-x-400"):
            out = film.apply_film(mid, slug, grain_scale=0.0, halation_scale=0.0)
            luma = float(np.median(out @ np.float32([0.2126, 0.7152, 0.0722])))
            self.assertGreater(luma, 0.30, slug)
            self.assertLess(luma, 0.58, slug)

    def test_more_light_prints_brighter_monotonically(self):
        levels = [0.05, 0.18, 0.5, 1.2]
        lumas = []
        for level in levels:
            out = film.apply_film(np.full((8, 8, 3), level, dtype=np.float32),
                                  "portra-400", grain_scale=0.0, halation_scale=0.0)
            lumas.append(float(out.mean()))
        self.assertEqual(lumas, sorted(lumas))

    def test_bw_stock_outputs_neutral_channels(self):
        rgb = np.random.default_rng(1).random((12, 12, 3)).astype(np.float32) * 0.6
        for slug in ("kodak-tri-x-400", "kodak-tmax-400"):
            out = film.apply_film(rgb, slug, grain_scale=0.0, halation_scale=0.0)
            np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1e-6)
            np.testing.assert_allclose(out[..., 1], out[..., 2], atol=1e-6)

    def test_halation_adds_red_glow_around_highlights(self):
        scene = np.full((64, 64, 3), 0.02, dtype=np.float32)
        scene[30:34, 30:34] = 8.0  # small specular source
        off = film.apply_film(scene, "cinestill-800t", grain_scale=0.0, halation_scale=0.0)
        on = film.apply_film(scene, "cinestill-800t", grain_scale=0.0, halation_scale=1.5)
        ring = (slice(22, 42), slice(22, 42))
        red_gain = float((on[ring][..., 0] - off[ring][..., 0]).mean())
        blue_gain = float((on[ring][..., 2] - off[ring][..., 2]).mean())
        self.assertGreater(red_gain, 0.005)
        self.assertGreater(red_gain, blue_gain * 2.0)

    def test_grain_is_deterministic_and_scaled(self):
        rgb = np.full((48, 48, 3), 0.18, dtype=np.float32)
        a = film.apply_film(rgb, "kodak-tri-x-400", halation_scale=0.0, grain_scale=1.0)
        b = film.apply_film(rgb, "kodak-tri-x-400", halation_scale=0.0, grain_scale=1.0)
        np.testing.assert_allclose(a, b)
        flat = film.apply_film(rgb, "kodak-tri-x-400", halation_scale=0.0, grain_scale=0.0)
        self.assertGreater(float(a.std()), float(flat.std()))

    def test_strength_zero_matches_neutral_srgb(self):
        rgb = np.random.default_rng(3).random((10, 10, 3)).astype(np.float32) * 0.8
        out = film.apply_film(rgb, "portra-400", strength=0.0, grain_scale=0.0, halation_scale=0.0)
        clipped = np.clip(rgb, 0, 1)
        neutral = np.where(clipped <= 0.0031308, clipped * 12.92,
                           1.055 * np.power(clipped, 1 / 2.4) - 0.055)
        np.testing.assert_allclose(out, neutral, atol=1e-5)


class NewStocks2026Tests(unittest.TestCase):
    """Coverage for the 2026-07-11 library expansion (NEW_STOCKS)."""

    def test_new_stocks_load_and_tables_build(self):
        for slug in NEW_STOCKS:
            stock = film.load_stock(slug)
            self.assertIsNotNone(stock, slug)
            tables = film.build_film_tables(stock)
            self.assertEqual(tables["hd_lut"].shape, (film.FILM_LUT_SIZE, 3), slug)
            self.assertEqual(tables["crosstalk"].shape, (3, 3), slug)
            self.assertEqual(tables["print_lut"].shape, (film.FILM_LUT_SIZE,), slug)

    def test_new_hd_curves_strictly_monotonic(self):
        # Negatives: density rises with exposure. Slide (positive): density falls.
        for slug in NEW_STOCKS:
            stock = film.load_stock(slug)
            negative = str(stock.get("type", "")).startswith("negative")
            for channel, points in stock["hd_curves"].items():
                pts = np.asarray(points, dtype=np.float64)
                self.assertTrue(np.all(np.diff(pts[:, 0]) > 0), f"{slug}:{channel} logE order")
                dd = np.diff(pts[:, 1])
                if negative:
                    self.assertTrue(np.all(dd > 0), f"{slug}:{channel} density not increasing")
                else:
                    self.assertTrue(np.all(dd < 0), f"{slug}:{channel} density not decreasing")

    def test_new_stocks_mid_gray_anchor(self):
        mid = np.full((16, 16, 3), film.FILM_MID_GRAY, dtype=np.float32)
        for slug in NEW_STOCKS:
            out = film.apply_film(mid, slug, grain_scale=0.0, halation_scale=0.0)
            luma = float(np.median(out @ np.float32([0.2126, 0.7152, 0.0722])))
            self.assertGreater(luma, 0.30, slug)
            self.assertLess(luma, 0.58, slug)

    def test_new_stocks_render_differs_from_no_film(self):
        rgb = np.random.default_rng(7).random((16, 16, 3)).astype(np.float32) * 0.9
        clipped = np.clip(rgb, 0, 1)
        neutral = np.where(clipped <= 0.0031308, clipped * 12.92,
                           1.055 * np.power(clipped, 1 / 2.4) - 0.055)
        for slug in NEW_STOCKS:
            out = film.apply_film(rgb, slug, grain_scale=0.0, halation_scale=0.0)
            diff = float(np.abs(out - neutral).mean())
            self.assertGreater(diff, 1e-3, slug)

    def test_new_stocks_more_light_prints_brighter(self):
        levels = [0.05, 0.18, 0.5, 1.2]
        for slug in NEW_STOCKS:
            lumas = []
            for level in levels:
                out = film.apply_film(np.full((8, 8, 3), level, dtype=np.float32),
                                      slug, grain_scale=0.0, halation_scale=0.0)
                lumas.append(float(out.mean()))
            self.assertEqual(lumas, sorted(lumas), slug)

    def test_velvia_is_positive_path(self):
        tables = film.build_film_tables(film.load_stock("fuji-velvia-50"))
        self.assertFalse(tables["negative"])
        self.assertFalse(tables["bw"])
        # slide: no orange mask
        np.testing.assert_allclose(tables["mask"], [0.0, 0.0, 0.0], atol=1e-9)


if __name__ == "__main__":
    unittest.main()
