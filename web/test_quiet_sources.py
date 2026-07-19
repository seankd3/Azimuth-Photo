"""Quiet sources: per-client exclude_sources for library browsing."""

from test_support import *  # noqa: F401,F403

from core.requests import parse_exclude_sources
from data.repositories import rankings as ranking_repository


class QuietSourcesTests(BackendTestCase):
    async def test_parse_exclude_sources_is_optional_and_stable(self):
        self.assertEqual(parse_exclude_sources(""), ())
        self.assertEqual(parse_exclude_sources(None), ())
        self.assertEqual(parse_exclude_sources("3,1,3,0,-2,x,2"), (3, 1, 2))
        self.assertEqual(parse_exclude_sources(["4", "1"]), (4, 1))

    async def test_ranking_filter_parts_exclude_sources(self):
        conditions, params = ranking_repository.ranking_filter_parts(exclude_sources=(2, 5))
        self.assertTrue(any("i.source_id NOT IN" in part for part in conditions))
        self.assertEqual(params[-2:], [2, 5])
        empty_conditions, empty_params = ranking_repository.ranking_filter_parts()
        self.assertFalse(any("i.source_id NOT IN" in part for part in empty_conditions))
        self.assertEqual(empty_params, [])

    async def test_exclude_sources_rankings_counts_and_absent_param(self):
        source_a = await self._source("quiet-a")
        source_b = await self._source("quiet-b")
        kept_a = await self._image(source_a["id"], "a.jpg", elo=1400)
        kept_b = await self._image(source_b["id"], "b.jpg", elo=1300)

        baseline = await library_routes.api_rankings(limit=10)
        self.assertEqual({image["id"] for image in baseline["images"]}, {kept_a, kept_b})

        excluded = await library_routes.api_rankings(limit=10, exclude_sources=str(source_a["id"]))
        self.assertEqual([image["id"] for image in excluded["images"]], [kept_b])

        counts = await library_routes.api_counts(exclude_sources=str(source_a["id"]))
        self.assertEqual(counts["total"], 1)

        histogram = await library_routes.api_date_histogram(exclude_sources=str(source_a["id"]))
        self.assertEqual(histogram["total"], 1)

        # date-groups shares ranking_filter_parts; prove the repository path excludes.
        groups = await ranking_repository.date_groups(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            exclude_sources=(source_a["id"],),
        )
        self.assertEqual(sum(group["count"] for group in groups), 1)

        markers = await library_routes.api_map_markers(exclude_sources=str(source_a["id"]))
        self.assertEqual(markers.get("total_count"), 1)

        unchanged = await library_routes.api_rankings(limit=10)
        self.assertEqual({image["id"] for image in unchanged["images"]}, {kept_a, kept_b})

    async def test_search_reports_hidden_in_quiet_sources(self):
        source_a = await self._source("quiet-search-a")
        source_b = await self._source("quiet-search-b")
        await self._image(source_a["id"], "sunset-a.jpg", elo=1400)
        await self._image(source_b["id"], "sunset-b.jpg", elo=1300)
        library_service._rankings_response_cache.clear()

        hidden = await library_routes.api_rankings(limit=10, q="sunset", exclude_sources=str(source_a["id"]))
        self.assertEqual(len(hidden["images"]), 1)
        self.assertEqual(hidden.get("hidden_in_quiet_sources"), 1)

        revealed = await library_routes.api_rankings(limit=10, q="sunset")
        self.assertEqual(len(revealed["images"]), 2)

    async def test_folder_scope_returns_source_photos(self):
        source_a = await self._source("quiet-folder-a")
        source_b = await self._source("quiet-folder-b")
        image_a = await self._image(source_a["id"], "nested/a.jpg", elo=1400)
        await self._image(source_b["id"], "b.jpg", elo=1300)

        scoped = await library_routes.api_rankings(limit=10, folder=source_a["path"])
        self.assertEqual([image["id"] for image in scoped["images"]], [image_a])

    async def test_sort_quality_honors_exclude_sources(self):
        source_a = await self._source("quiet-quality-a")
        source_b = await self._source("quiet-quality-b")
        await self._image(source_a["id"], "qa.jpg", elo=1400, comparisons=8)
        await self._image(source_b["id"], "qb.jpg", elo=1300, comparisons=8)
        library_service._rankings_response_cache.clear()

        all_photos = await library_routes.api_rankings(limit=10)
        excluded = await library_routes.api_rankings(limit=10, exclude_sources=str(source_a["id"]))
        self.assertIsNotNone(all_photos.get("sort_quality"))
        self.assertIsNotNone(excluded.get("sort_quality"))
        self.assertEqual(int(all_photos["sort_quality"]["total"]), 2)
        self.assertEqual(int(excluded["sort_quality"]["total"]), 1)
