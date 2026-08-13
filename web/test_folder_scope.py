"""A multi-folder selection must reach every route intact.

The sidebar sends one `folder=` per selected node. A route declaring
`folder: str` receives the last of them, so a three-folder selection silently
became a one-folder query. Refine was the visible symptom: 195 photos selected,
"Not enough photos to refine", because the last of the three folders held five
and a 3x2 mosaic needs six.
"""

from __future__ import annotations

import inspect
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.requests import FolderScope, RankingSort


class FolderScopeTests(unittest.TestCase):
    def test_every_selected_folder_arrives(self):
        app = FastAPI()

        @app.get("/scoped")
        def scoped(folder: FolderScope = ""):
            return {"folder": folder}

        client = TestClient(app)
        self.assertEqual(
            client.get("/scoped?folder=a&folder=b&folder=c").json()["folder"],
            ["a", "b", "c"],
        )
        # One folder stays a plain string: the query layer takes either, and a
        # one-element list would be a needless shape change for every caller.
        self.assertEqual(client.get("/scoped?folder=a").json()["folder"], "a")
        self.assertEqual(client.get("/scoped").json()["folder"], "")
        self.assertEqual(client.get("/scoped?folder=&folder=b").json()["folder"], "b")


class RoutesDeclareTheScopeTests(unittest.TestCase):
    """The point of the dependency is that it cannot be forgotten."""

    def test_no_route_takes_a_bare_folder_string(self):
        from features.compare import routes as compare_routes
        from features.export import routes as export_routes
        from features.library import routes as library_routes
        from features.search import routes as search_routes

        offenders = []
        for module in (compare_routes, export_routes, library_routes, search_routes):
            for name, function in vars(module).items():
                if not callable(function) or name.startswith("_"):
                    continue
                try:
                    signature = inspect.signature(function)
                except (TypeError, ValueError):
                    continue
                parameter = signature.parameters.get("folder")
                if parameter is None:
                    continue
                if parameter.annotation is str:
                    offenders.append(f"{module.__name__}.{name}")
        self.assertEqual(
            offenders,
            [],
            "these routes would keep only the last selected folder; "
            "declare folder: FolderScope instead",
        )



class RankingSortTests(unittest.TestCase):
    """A sort the server cannot honour is a 400, not quietly Elo order.

    `RANKING_SORTS.get(sort, "elo DESC")` had no allowlist and nothing upstream
    rejected anything, so shipping a UI sort option without its registry entry
    ordered the grid by Elo while the control read "Lens" — no error, no log.
    """

    def _client(self):
        app = FastAPI()

        @app.get("/sorted")
        def sorted_route(sort: RankingSort = "elo"):
            return {"sort": sort}

        return TestClient(app)

    def test_a_sort_the_registry_knows_is_passed_through(self):
        client = self._client()
        for name in ("elo", "date_taken", "filename_desc", "least_compared"):
            self.assertEqual(client.get(f"/sorted?sort={name}").json()["sort"], name, name)

    def test_a_sort_the_registry_does_not_know_is_rejected(self):
        client = self._client()
        for name in ("lens", "elo desc", "'; DROP TABLE images;--", "ELO"):
            self.assertEqual(client.get(f"/sorted?sort={name}").status_code, 400, name)

    def test_the_computed_sorts_are_part_of_the_vocabulary(self):
        """taste and similarity are honoured by the service, not the registry.

        The first strict allowlist was built from RANKING_SORTS alone and
        rejected them — which silently disabled the Taste sort everywhere,
        because the UI's availability probe is a `sort=taste&limit=0` request
        whose 400 was swallowed by its `.catch(() => null)`.
        """

        client = self._client()
        for name in ("taste", "similarity"):
            self.assertEqual(client.get(f"/sorted?sort={name}").json()["sort"], name, name)

    def test_no_sort_still_means_elo(self):
        client = self._client()
        self.assertEqual(client.get("/sorted").json()["sort"], "elo")
        self.assertEqual(client.get("/sorted?sort=").json()["sort"], "elo")

    def test_every_route_taking_a_sort_declares_the_registry(self):
        """The point of the dependency is that it cannot be forgotten."""

        from features.export import routes as export_routes
        from features.library import routes as library_routes

        offenders = []
        for module in (export_routes, library_routes):
            for name, function in vars(module).items():
                if not callable(function) or name.startswith("_"):
                    continue
                try:
                    parameter = inspect.signature(function).parameters.get("sort")
                except (TypeError, ValueError):
                    continue
                if parameter is not None and parameter.annotation is str:
                    offenders.append(f"{module.__name__}.{name}")
        self.assertEqual(
            offenders,
            [],
            "these routes would accept any sort name and silently order by Elo; "
            "declare sort: RankingSort instead",
        )


class DateScopeTests(unittest.TestCase):
    """A date scope the server cannot read must match nothing, not everything.

    The Timeline sends a day ("2024-05-01") when a day row is clicked. The range
    builder understood only years and months, returned None, and the caller then
    added no date condition at all — so the grid showed the whole library while
    the context chip read the date that had been clicked. Nothing in the UI
    contradicted it, because every library route shares the filter builder.
    """

    def test_every_precision_the_ui_can_send_is_understood(self):
        from data.repositories.rankings import date_taken_filter_range

        self.assertEqual(
            date_taken_filter_range("2024"),
            ("2024-01-01 00:00:00", "2025-01-01 00:00:00"),
        )
        self.assertEqual(
            date_taken_filter_range("2024-05"),
            ("2024-05-01 00:00:00", "2024-06-01 00:00:00"),
        )
        self.assertEqual(
            date_taken_filter_range("2024-05-01"),
            ("2024-05-01 00:00:00", "2024-05-02 00:00:00"),
        )
        # Rollovers the three-branch version had to special-case one at a time.
        self.assertEqual(
            date_taken_filter_range("2024-12-31"),
            ("2024-12-31 00:00:00", "2025-01-01 00:00:00"),
        )
        self.assertEqual(
            date_taken_filter_range("2024-02-29"),
            ("2024-02-29 00:00:00", "2024-03-01 00:00:00"),
        )

    def test_an_unreadable_scope_is_not_a_date(self):
        from data.repositories.rankings import date_taken_filter_range

        for value in ("garbage", "2024-05-01T00:00:00", "2024-13", ""):
            self.assertIsNone(date_taken_filter_range(value), value)

    def test_an_unreadable_scope_matches_nothing_rather_than_everything(self):
        import helpers

        images = [{"date_taken": "2024-05-01 10:00:00"}, {"date_taken": "2022-02-02 10:00:00"}]
        self.assertEqual(helpers.filter_by_metadata(images, date_taken="garbage"), [])
        self.assertEqual(len(helpers.filter_by_metadata(images, date_taken="2024-05-01")), 1)
        self.assertEqual(len(helpers.filter_by_metadata(images, date_taken="")), 2)


if __name__ == "__main__":
    unittest.main()
