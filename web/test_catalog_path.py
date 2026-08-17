"""There is one catalog, and one way to ask where it is.

Twenty-eight modules used to keep a private `_db_path` global, a
`configure(db_path=...)` setter, a `_configured_db_path()` reader that raised
if nobody had called the setter, and a line in `wiring.py` — and every one of
those wiring lines passed the same `lambda: catalog_path()`.

The indirection existed so tests could point at a temporary catalog, which they
already do by setting `catalog_path()`. So it bought nothing and cost a concept: a
module could be imported but "not configured", and several modules treated that
as normal — returning quietly instead of writing to the oplog.
"""

import pathlib
import unittest

import db
from core.catalog_path import catalog_path, use as catalog_path_use

WEB = pathlib.Path(__file__).parent
SKIPPED = {".venv", "node_modules", "__pycache__", "build", "dist"}


def _production_modules():
    for path in WEB.rglob("*.py"):
        if SKIPPED.intersection(path.parts) or path.name.startswith(("test_", "bench")):
            continue
        yield path


class CatalogPathTests(unittest.TestCase):
    def setUp(self):
        self.original = catalog_path()
        self.addCleanup(catalog_path_use, self.original)

    def test_it_is_the_path_the_process_is_working_with(self):
        catalog_path_use("X:/library/catalog.db")
        self.assertEqual(catalog_path(), "X:/library/catalog.db")

    def test_it_follows_a_change_rather_than_caching_one(self):
        """A test switching catalogs must not need to reconfigure anything."""

        catalog_path_use("X:/first.db")
        self.assertEqual(catalog_path(), "X:/first.db")
        catalog_path_use("X:/second.db")
        self.assertEqual(catalog_path(), "X:/second.db")


class NobodyKeepsAPrivateCopyTests(unittest.TestCase):
    def test_no_module_keeps_its_own_catalog_path(self):
        strays = [
            str(path.relative_to(WEB)).replace("\\", "/")
            for path in _production_modules()
            if "global _db_path" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(strays, [], f"private catalog paths returned: {strays}")

    def test_nothing_is_configured_with_the_catalog_path_any_more(self):
        strays = [
            str(path.relative_to(WEB)).replace("\\", "/")
            for path in _production_modules()
            if "db_path=lambda: catalog_path()" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(strays, [], f"wiring still passes the path: {strays}")

    def test_no_module_can_be_imported_but_unconfigured(self):
        """The state that only ever meant a missing line in wiring.py."""

        strays = [
            str(path.relative_to(WEB)).replace("\\", "/")
            for path in _production_modules()
            if "def _configured_db_path(" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(strays, [], f"private readers returned: {strays}")


if __name__ == "__main__":
    unittest.main()
