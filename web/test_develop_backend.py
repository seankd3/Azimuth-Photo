import asyncio
import gzip
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

try:
    import pytest
except ImportError:  # Keep the repository's unittest fallback runnable in minimal venvs.
    class _PytestMark:
        @staticmethod
        def skipif(condition, reason):
            return unittest.skipIf(condition, reason)

    class _PytestFallback:
        mark = _PytestMark()

    pytest = _PytestFallback()

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
from features.develop import rawproc, routes as develop_routes  # noqa: E402
from features.sync import readthrough  # noqa: E402


RAW_ROOT = Path("/mnt/expansion/Photos/RAWS")


class DevelopBackendTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        db.DB_PATH = os.path.join(self.tempdir.name, "develop.db")
        rawproc.BASE_CACHE_ROOT = Path(self.tempdir.name) / "develop-cache"
        rawproc.BASE_CACHE_DIR = rawproc.BASE_CACHE_ROOT / "base" / "v2"
        rawproc._recent_decodes.clear()
        develop_routes._base_generation_failures.clear()
        asyncio.run(db.init_db())
        source = asyncio.run(db.add_or_restore_source(os.path.join(self.tempdir.name, "raws")))
        self.raw_path = Path(self.tempdir.name) / "raws" / "sample.dng"
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_path.write_bytes(b"not decoded in settings tests")
        self.raw_id = self._image(source["id"], self.raw_path)
        self.jpg_path = Path(self.tempdir.name) / "raws" / "sample.jpg"
        self.jpg_id = self._image(source["id"], self.jpg_path)
        Image.new("RGB", (8, 6), (128, 128, 128)).save(self.jpg_path, quality=100, subsampling=0)
        self.unsupported_id = self._image(source["id"], Path(self.tempdir.name) / "raws" / "sample.xyz")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.old_db_path
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc._recent_decodes.clear()
        develop_routes._base_generation_failures.clear()
        self.tempdir.cleanup()

    def _image(self, source_id, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        async def insert():
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                    (source_id, path.name, str(path)),
                )
                await conn.commit()
                return cursor.lastrowid
            finally:
                await conn.close()
        return asyncio.run(insert())

    def test_settings_round_trip_preserves_unknown_keys_and_appends_history(self):
        first = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": {"Exposure2012": 1.25, "FutureCrsKey": {"keep": True}}, "label": "Exposure"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": {"Exposure2012": -0.5}, "label": "Exposure"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["settings"]["FutureCrsKey"], {"keep": True})

        async def read_row():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (self.raw_id,))
                return (await cursor.fetchone())["settings"]
            finally:
                await conn.close()
        stored = json.loads(asyncio.run(read_row()))
        self.assertEqual(stored["FutureCrsKey"], {"keep": True})
        self.assertGreaterEqual(len(asyncio.run(self._history_rows())), 2)

    def test_film_settings_round_trip_and_export_changes_pixels(self):
        from features.develop import render as develop_render

        film_settings = {
            "pa_FilmStock": "cinestill-800t",
            "pa_FilmStrength": 100,
            "pa_FilmHalation": 100,
            "pa_FilmGrain": 100,
            "pa_FilmGrainSize": 100,
        }
        saved = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": film_settings, "label": "Film: CineStill 800T"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self._write_cached_base()
        loaded = self.client.get(f"/api/develop/{self.raw_id}")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertEqual({key: loaded.json()["settings"][key] for key in film_settings}, film_settings)

        old_export = develop_render.EXPORT_DIRECTORY
        develop_render.EXPORT_DIRECTORY = Path(self.tempdir.name) / "film-exports"
        y, x = np.mgrid[0:48, 0:64].astype(np.float32)
        linear = np.stack((0.03 + x / 50, 0.02 + y / 60, 0.04 + (x + y) / 110), axis=-1)
        linear[20:28, 28:36] = 3.0
        try:
            with mock.patch.object(develop_render, "decode_full_resolution", return_value=linear):
                film_export = self.client.post(
                    f"/api/develop/{self.raw_id}/export",
                    json={"format": "jpeg", "quality": 100, "sharpen": "none"},
                )
                disabled = self.client.put(
                    f"/api/develop/{self.raw_id}",
                    json={"settings": {"pa_FilmStock": None}, "label": "Film: None"},
                )
                digital_export = self.client.post(
                    f"/api/develop/{self.raw_id}/export",
                    json={"format": "jpeg", "quality": 100, "sharpen": "none"},
                )
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertEqual(film_export.status_code, 200, film_export.text)
            self.assertEqual(digital_export.status_code, 200, digital_export.text)
            film_pixels = np.asarray(Image.open(io.BytesIO(film_export.content)).convert("RGB"), dtype=np.float32)
            digital_pixels = np.asarray(Image.open(io.BytesIO(digital_export.content)).convert("RGB"), dtype=np.float32)
            self.assertGreater(float(np.mean(np.abs(film_pixels - digital_pixels))), 5.0)
        finally:
            develop_render.EXPORT_DIRECTORY = old_export

    def test_film_pipeline_bypasses_base_and_user_tone_curves(self):
        from features.develop import pipeline

        linear = np.full((12, 16, 3), 0.18, dtype=np.float32)
        settings = {
            "pa_FilmStock": "portra-400",
            "pa_FilmGrain": 0,
            "pa_FilmHalation": 0,
            "Sharpness": 0,
            "ToneCurvePV2012": ["0, 0", "128, 220", "255, 255"],
        }
        with (
            mock.patch.object(
                pipeline,
                "apply_film",
                side_effect=lambda rgb, *_args, **_kwargs: pipeline.linear_to_srgb(rgb),
            ) as film_stage,
            mock.patch.object(pipeline, "_apply_tone_curves", wraps=pipeline._apply_tone_curves) as tone_stage,
        ):
            pipeline.apply_pipeline(linear, settings)
        film_stage.assert_called_once()
        tone_stage.assert_not_called()

    def test_look_round_trip_keeps_the_imported_object_verbatim(self):
        look = {
            "Name": "Agfa Precisa 100 II",
            "Amount": 0.65,
            "Parameters": {
                "Clarity2012": 12,
                "ConvertToGrayscale": False,
                "ToneCurvePV2012": ["0, 0", "128, 150", "255, 255"],
                "RGBTable": "unavailable-table-id",
            },
        }
        saved = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": {"Clarity2012": -4, "Look": look}, "label": "Imported Look"},
        )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["settings"]["Look"], look)
        self.assertEqual(saved.json()["settings"]["Clarity2012"], -4)

    def test_history_read_is_capped_at_forty(self):
        for index in range(45):
            response = self.client.put(
                f"/api/develop/{self.raw_id}",
                json={"settings": {"Exposure2012": index / 10}, "label": f"Step {index}"},
            )
            self.assertEqual(response.status_code, 200)
        # Avoid a real decode here; base cache metadata is enough for the GET contract.
        self._write_cached_base()
        response = self.client.get(f"/api/develop/{self.raw_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["history"]), 40)

    def test_named_snapshots_are_not_truncated_by_edit_history(self):
        for label in ("Print", "Web"):
            saved = self.client.post(
                f"/api/develop/{self.raw_id}/snapshots",
                json={"label": label, "settings": {"Exposure2012": 0.5}},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        for index in range(45):
            response = self.client.put(
                f"/api/develop/{self.raw_id}",
                json={"settings": {"Exposure2012": index / 10}, "label": f"Step {index}"},
            )
            self.assertEqual(response.status_code, 200, response.text)

        snapshots = self.client.get(f"/api/develop/{self.raw_id}/snapshots")

        self.assertEqual(snapshots.status_code, 200, snapshots.text)
        self.assertEqual(
            [row["label"] for row in snapshots.json()["snapshots"]],
            ["Snapshot: Web", "Snapshot: Print"],
        )

    def test_base_endpoints_and_pregen_contract(self):
        self._write_cached_base()
        # A warm preview must be a pure disk response: the route may not enter
        # the RAW decoder before the browser gets its first visible image.
        with mock.patch.object(rawproc, "ensure_base_cache", side_effect=AssertionError("must not decode warm base")):
            binary = self.client.get(f"/api/develop/{self.raw_id}/base.bin")
            preview = self.client.get(f"/api/develop/{self.raw_id}/base.jpg")
        pregen = self.client.post("/api/develop/pregen", json={"image_ids": []})
        self.assertEqual(binary.status_code, 200)
        self.assertEqual(binary.headers["content-encoding"], "gzip")
        # TestClient follows Content-Encoding semantics and hands callers the
        # decoded body, exactly as browser fetch does.
        parsed, width, height = rawproc.parse_base_payload(binary.content)
        self.assertEqual((width, height, parsed.dtype), (1, 1, np.dtype("<u2")))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/jpeg")
        self.assertEqual(pregen.status_code, 202)
        self.assertEqual(pregen.json()["queued"], [])

    def test_cold_base_artifacts_start_background_generation_and_return_202(self):
        with mock.patch.object(develop_routes, "_start_base_generation") as start_generation:
            preview = self.client.get(f"/api/develop/{self.raw_id}/base.jpg")
            binary = self.client.get(f"/api/develop/{self.raw_id}/base.bin")
        self.assertEqual(preview.status_code, 202, preview.text)
        self.assertEqual(binary.status_code, 202, binary.text)
        self.assertEqual(preview.json()["state"], "generating")
        self.assertEqual(binary.headers["retry-after"], "1")
        self.assertEqual(start_generation.call_count, 2)

    def test_remote_settings_get_does_not_wait_for_hub_base(self):
        async def mark_remote():
            conn = await db.get_db()
            try:
                await conn.execute("UPDATE images SET hub_remote = 1 WHERE id = ?", (self.raw_id,))
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(mark_remote())
        self.raw_path.unlink()
        with mock.patch.object(rawproc, "ensure_base_cache", side_effect=AssertionError("must not contact hub")):
            response = self.client.get(f"/api/develop/{self.raw_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["meta"], {"canvas_color_profile": {}})

    def test_base_poll_surfaces_recent_hub_failure_as_503(self):
        cause = readthrough.BaseReadthroughError("Could not reach the hub for this Develop base")
        failure = rawproc.RawDecodeError(str(cause))
        failure.__cause__ = cause
        develop_routes._base_generation_failures[self.raw_id] = (develop_routes.time.monotonic(), failure)

        response = self.client.get(f"/api/develop/{self.raw_id}/base.bin")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["reason"], "hub_unreachable")

    def test_transform_auto_with_cold_base_returns_pending(self):
        with mock.patch.object(develop_routes, "_start_base_generation") as start_generation:
            response = self.client.post(f"/api/develop/{self.raw_id}/transform/auto")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "pending")
        start_generation.assert_called_once()

    def test_remote_auto_with_uncached_base_returns_202_without_waiting_for_hub(self):
        async def mark_remote():
            conn = await db.get_db()
            try:
                await conn.execute("UPDATE images SET hub_remote = 1 WHERE id = ?", (self.raw_id,))
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(mark_remote())
        self.raw_path.unlink()
        ensure_started = threading.Event()
        release_ensure = threading.Event()
        ensure_finished = threading.Event()

        def slow_hub_ensure(*_args, **_kwargs):
            ensure_started.set()
            release_ensure.wait(timeout=2)
            ensure_finished.set()

        release_timer = threading.Timer(1.25, release_ensure.set)
        release_timer.start()
        try:
            with mock.patch.object(rawproc, "ensure_base_cache", side_effect=slow_hub_ensure):
                async def exercise_route():
                    started = time.perf_counter()
                    response = await develop_routes.api_develop_auto_tone(self.raw_id)
                    elapsed = time.perf_counter() - started
                    self.assertTrue(await asyncio.to_thread(ensure_started.wait, 1))
                    release_ensure.set()
                    self.assertTrue(await asyncio.to_thread(ensure_finished.wait, 1))
                    await asyncio.sleep(0)
                    return response, elapsed

                response, elapsed = asyncio.run(exercise_route())
        finally:
            release_timer.cancel()
            release_ensure.set()

        self.assertEqual(response.status_code, 202, response.body)
        self.assertEqual(json.loads(response.body)["status"], "pending")
        self.assertLess(elapsed, 1.0)

    def test_get_meta_self_heals_camera_profile_and_lens_data(self):
        self._write_cached_base()
        fitted = {
            "slug": "canon-eos-r5",
            "model": "Canon EOS R5",
            "tone_nodes": [index / 15 for index in range(16)],
            "tone_values": [index / 15 for index in range(16)],
            "oklab_ab_delta": np.zeros((12, 3, 2)).tolist(),
            "chroma_edges": [0.02, 0.06, 0.12, 1.0],
        }
        correction = {"distortion": {"model": "ptlens", "terms": [0.01, 0.02, 0.03]}}
        exif = {
            "Make": "Canon",
            "UniqueCameraModel": "Canon EOS R5",
            "LensModel": "RF24-105mm F4 L IS USM",
            "FocalLength": 37,
            "FNumber": 11,
        }
        with (
            mock.patch.object(rawproc, "read_exif", return_value=exif),
            mock.patch.object(rawproc, "load_camera_profile", return_value=fitted),
            mock.patch.object(rawproc, "resolve_lens_correction", return_value=correction),
        ):
            response = self.client.get(f"/api/develop/{self.raw_id}")
        self.assertEqual(response.status_code, 200)
        meta = response.json()["meta"]
        self.assertEqual(meta["camera_model"], "Canon EOS R5")
        self.assertEqual(meta["camera_profile"]["slug"], "canon-eos-r5")
        self.assertEqual(meta["lens_correction"]["distortion"]["model"], "ptlens")
        self.assertEqual(meta["color"]["camera_profile"]["slug"], "canon-eos-r5")

    async def _history_rows(self):
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT id FROM develop_history WHERE image_id = ?", (self.raw_id,))
            return await cursor.fetchall()
        finally:
            await conn.close()

    def _write_cached_base(self):
        paths = rawproc.base_paths(self.raw_id)
        paths.binary.parent.mkdir(parents=True, exist_ok=True)
        payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, 1, 1) + b"\0" * 6
        paths.binary.write_bytes(gzip.compress(payload))
        paths.preview.write_bytes(b"jpg")
        paths.metadata.write_text(json.dumps({"as_shot": {"temperature": 5500, "tint": 0}}))

    def test_base_cache_header_parse(self):
        rgb = np.arange(18, dtype=np.uint16).reshape(2, 3, 3)
        payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, 3, 2) + rgb.astype("<u2").tobytes()
        parsed, width, height = rawproc.parse_base_payload(payload)
        self.assertEqual((width, height), (3, 2))
        np.testing.assert_array_equal(parsed, rgb)

    def test_display_extensions_follow_pillow_heic_support(self):
        formats = {
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "png": "PNG",
            "tif": "TIFF",
            "tiff": "TIFF",
            "webp": "WEBP",
        }
        for extension, image_format in formats.items():
            with self.subTest(extension=extension):
                self.assertTrue(rawproc.is_develop_path(f"photo.{extension}"))
                path = Path(self.tempdir.name) / f"display.{extension}"
                Image.new("RGB", (5, 4), (96, 128, 160)).save(path, format=image_format)
                rgb, meta = rawproc.decode_base(path)
                self.assertEqual(
                    (rgb.shape, rgb.dtype, meta["base_kind"]),
                    ((4, 5, 3), np.dtype(np.uint16), "display"),
                )
        heic_supported = ".heic" in Image.registered_extensions()
        self.assertEqual(rawproc.is_develop_path("photo.heic"), heic_supported)

    def test_jpeg_decode_creates_a_linear_display_base_without_raw_profile_metadata(self):
        with (
            mock.patch.object(rawproc, "read_exif") as read_exif,
            mock.patch.object(rawproc, "load_camera_profile") as load_camera_profile,
            mock.patch.object(rawproc, "resolve_lens_correction") as resolve_lens_correction,
        ):
            rgb, meta = rawproc.decode_base(self.jpg_path)

        self.assertEqual((rgb.shape, rgb.dtype), ((6, 8, 3), np.dtype(np.uint16)))
        self.assertEqual(meta["base_kind"], "display")
        self.assertEqual(meta["as_shot"], {"temperature": 6500, "tint": 0.0, "method": "display"})
        self.assertTrue(meta["linear"])
        self.assertNotIn("color", meta)
        read_exif.assert_not_called()
        load_camera_profile.assert_not_called()
        resolve_lens_correction.assert_not_called()

        round_trip = np.rint(rawproc._linear_to_srgb(rgb.astype(np.float32) / 65535.0) * 255.0)
        self.assertLessEqual(float(np.mean(np.abs(round_trip - 128.0))), 1.0)

    def test_jpeg_develop_get_and_base_endpoints(self):
        settings = self.client.get(f"/api/develop/{self.jpg_id}")
        binary = self.client.get(f"/api/develop/{self.jpg_id}/base.bin")
        preview = self.client.get(f"/api/develop/{self.jpg_id}/base.jpg")

        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json()["meta"]["base_kind"], "display")
        parsed, width, height = rawproc.parse_base_payload(binary.content)
        self.assertEqual((binary.status_code, width, height, parsed.dtype), (200, 8, 6, np.dtype("<u2")))
        self.assertEqual(preview.status_code, 200)

    def test_mired_white_balance_math(self):
        neutral = rawproc.estimate_as_shot_white_balance_mired([2.0, 1.0, 1.0, 0.0], [2.0, 1.0, 1.0, 0.0])
        cooler = rawproc.estimate_as_shot_white_balance_mired([4.0, 1.0, 1.0, 0.0], [2.0, 1.0, 1.0, 0.0])
        self.assertEqual(neutral["temperature"], 5500)
        self.assertLess(cooler["temperature"], neutral["temperature"])
        self.assertIn("camera_whitebalance", cooler)
        self.assertEqual(neutral["method"], "mired")

    def test_dng_mccamy_white_balance_math(self):
        # ColorMatrix2 + AsShotNeutral from a measured daylight-ish DNG.
        asn = [0.501961, 1.0, 0.554713]
        cm2 = [
            0.9766, -0.2953, -0.1254,
            -0.4276, 1.2116, 0.2433,
            -0.0437, 0.1336, 0.5131,
        ]
        result = rawproc.estimate_as_shot_white_balance(
            [asn[1] / asn[0], 1.0, asn[1] / asn[2], 0.0],
            [],
            as_shot_neutral=asn,
            color_matrix2=cm2,
        )
        self.assertEqual(result["method"], "dng_mccamy")
        self.assertAlmostEqual(result["temperature"], 5613, delta=25)
        self.assertGreaterEqual(result["tint"], -30)
        self.assertLessEqual(result["tint"], 30)

    def test_reset_restores_the_xmp_baseline(self):
        async def seed_xmp():
            conn = await db.get_db()
            try:
                await conn.execute(
                    "INSERT INTO develop_settings (image_id, settings, origin, updated_at) VALUES (?, ?, 'xmp', ?)",
                    (self.raw_id, json.dumps({"Exposure2012": 0.25, "FutureCrsKey": "kept"}), "2026-07-10T00:00:00+00:00"),
                )
                await conn.commit()
            finally:
                await conn.close()
        asyncio.run(seed_xmp())
        changed = self.client.put(f"/api/develop/{self.raw_id}", json={"settings": {"Exposure2012": 2}})
        reset = self.client.post(f"/api/develop/{self.raw_id}/reset")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json()["origin"], "xmp")
        self.assertEqual(reset.json()["settings"], {"Exposure2012": 0.25, "FutureCrsKey": "kept"})

    def test_honest_unsupported_and_missing_errors(self):
        unsupported = self.client.get(f"/api/develop/{self.unsupported_id}")
        self.assertEqual(unsupported.status_code, 400)
        self.assertIn("Pillow", unsupported.json()["error"])
        self.raw_path.unlink()
        missing = self.client.get(f"/api/develop/{self.raw_id}/base.jpg")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"], "Source image file is unavailable")

    def test_export_honors_resize_quality_and_sharpen_byte_sizes(self):
        from features.develop import render as develop_render

        self._write_cached_base()
        export_root = Path(self.tempdir.name) / "exports"
        library_root = Path(self.tempdir.name) / "library-exports"
        old_export = develop_render.EXPORT_DIRECTORY
        old_library = develop_render.LIBRARY_EXPORT_DIRECTORY
        develop_render.EXPORT_DIRECTORY = export_root
        develop_render.LIBRARY_EXPORT_DIRECTORY = library_root
        linear = np.linspace(0.05, 0.95, 48 * 64 * 3, dtype=np.float32).reshape(48, 64, 3)
        try:
            with (
                mock.patch.object(develop_render, "decode_full_resolution", return_value=linear.copy()),
                mock.patch.object(
                    develop_render,
                    "_apply_pipeline_tiled",
                    side_effect=lambda rgb, *args, **kwargs: np.asarray(rgb, dtype=np.float32).copy(),
                ),
            ):
                low = self.client.post(
                    f"/api/develop/{self.raw_id}/export",
                    json={"format": "jpeg", "quality": 40, "max_px": 32, "sharpen": "none", "filename_pattern": "{stem}-q40"},
                )
                high = self.client.post(
                    f"/api/develop/{self.raw_id}/export",
                    json={
                        "format": "jpeg",
                        "quality": 95,
                        "max_px": 64,
                        "sharpen": "print_high",
                        "filename_pattern": "{stem}-q95",
                    },
                )
                batch = self.client.post(
                    "/api/develop/export/batch",
                    json={"image_ids": [self.raw_id], "format": "jpeg", "quality": 80, "sharpen": "screen_low"},
                )
            self.assertEqual(low.status_code, 200, low.text)
            self.assertEqual(high.status_code, 200, high.text)
            self.assertEqual(batch.status_code, 202, batch.text)
            self.assertLess(len(low.content), len(high.content))
            self.assertIn("sample-q40", low.headers.get("content-disposition", ""))
            self.assertIn("sample-q95", high.headers.get("content-disposition", ""))
            # Sharpened full-edge export should differ from the small unsharp one.
            self.assertNotEqual(low.content, high.content)
            status = self.client.get("/api/develop/export/batch/status")
            self.assertEqual(status.status_code, 200)
            self.assertIn(status.json()["state"], {"running", "complete", "idle"})
        finally:
            develop_render.EXPORT_DIRECTORY = old_export
            develop_render.LIBRARY_EXPORT_DIRECTORY = old_library

    def test_filename_pattern_and_output_sharpen_helpers(self):
        from features.develop import render as develop_render

        name = develop_render.format_export_filename(
            raw_path="/tmp/RAWS/demo.dng",
            image_id=42,
            output_format="jpeg",
            pattern="{stem}_{id}_{date}",
        )
        self.assertTrue(name.startswith("demo_42_"))
        self.assertTrue(name.endswith(".jpg"))
        rgb = np.linspace(0.1, 0.9, 16 * 16 * 3, dtype=np.float32).reshape(16, 16, 3)
        sharpened = develop_render.apply_output_sharpen(rgb, "screen_high")
        untouched = develop_render.apply_output_sharpen(rgb, "none")
        self.assertEqual(untouched.shape, rgb.shape)
        self.assertFalse(np.allclose(sharpened, rgb))
        self.assertTrue(np.allclose(untouched, rgb))


@pytest.mark.skipif(not RAW_ROOT.exists(), reason="expansion RAW library is not mounted")
def test_real_dng_decode_has_linear_uint16_base():
    # Some Lightroom-created DNGs contain only a reduced preview that LibRaw
    # correctly rejects. Probe until the mounted library yields one full RAW.
    rgb = meta = None
    for dng in RAW_ROOT.rglob("*.dng"):
        try:
            rgb, meta = rawproc.decode_base(dng)
            break
        except rawproc.RawDecodeError:
            continue
    assert rgb is not None and meta is not None, "mounted RAW library contains no LibRaw-decodable DNG"
    assert rgb.dtype == np.uint16
    assert rgb.ndim == 3 and rgb.shape[2] == 3
    assert max(rgb.shape[:2]) <= rawproc.MAX_BASE_EDGE
    assert meta["linear"] is True
