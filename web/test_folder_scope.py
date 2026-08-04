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

from core.requests import FolderScope


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


if __name__ == "__main__":
    unittest.main()
