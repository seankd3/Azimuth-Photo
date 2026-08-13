"""The recipe is one hash of exactly its declared inputs."""

import inspect
import unittest

from pixels import rendition


BASE = dict(cache_version="v9", kind="sm", pixels=400, quality=82, develop_fingerprint="")


class RecipeTests(unittest.TestCase):
    def test_same_inputs_same_recipe(self):
        self.assertEqual(rendition.recipe_for(**BASE), rendition.recipe_for(**BASE))

    def test_every_declared_input_changes_the_recipe(self):
        """Each input independently changes the bytes, so each must change the key.

        If a new input is added to recipe_for without extending this table, the
        enumeration below fails — extending the recipe is a deliberate act.
        """

        changed = {
            "cache_version": "v10",
            "kind": "md",
            "pixels": 1024,
            "quality": 90,
            "develop_fingerprint": "2026-08-13T18:00:00",
        }
        base_recipe = rendition.recipe_for(**BASE)
        for name, value in changed.items():
            with self.subTest(input=name):
                self.assertNotEqual(base_recipe, rendition.recipe_for(**{**BASE, name: value}))
        self.assertEqual(set(changed), set(BASE), "keep the two tables in step")

    def test_the_input_list_is_closed(self):
        """recipe_for accepts exactly the declared inputs — nothing positional,
        nothing extra. A recipe input that can be forgotten at a call site is a
        stale tile served forever with nothing to detect it."""

        parameters = inspect.signature(rendition.recipe_for).parameters
        self.assertEqual(set(parameters) - {"self"}, set(BASE))
        for parameter in parameters.values():
            self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_an_edit_changes_the_key_a_rename_does_not(self):
        """The product sentence: edits invalidate, file moves never do."""

        unedited = rendition.recipe_for(**BASE)
        edited = rendition.recipe_for(**{**BASE, "develop_fingerprint": "1786660000.0"})
        self.assertNotEqual(unedited, edited)
        # There is no path/mtime/id input to vary — that is the point.
        self.assertNotIn("path", inspect.signature(rendition.recipe_for).parameters)
