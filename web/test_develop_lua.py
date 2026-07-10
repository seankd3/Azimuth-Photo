import unittest

from features.develop.lua_table import parse_lua_table


class LightroomLuaTableTests(unittest.TestCase):
    def test_parses_catalog_literals_without_executing_lua(self):
        result = parse_lua_table(
            r'''s = {
                Title = "She said: \"hello\"\\again",
                Exposure2012 = -0.75,
                Enabled = true,
                Disabled = false,
                ["x-default"] = { 1, -2.5, { Name = "nested" } },
                Nested = { { 1, 2 }, { 3, 4 } },
                Mixed = { "first", Name = "second", ["quoted"] = 3 },
            }'''
        )
        self.assertEqual(result["Title"], 'She said: "hello"\\again')
        self.assertEqual(result["Exposure2012"], -0.75)
        self.assertIs(result["Enabled"], True)
        self.assertIs(result["Disabled"], False)
        self.assertEqual(result["x-default"], [1, -2.5, {"Name": "nested"}])
        self.assertEqual(result["Nested"], [[1, 2], [3, 4]])
        self.assertEqual(result["Mixed"], {1: "first", "Name": "second", "quoted": 3})

    def test_returns_lists_for_positional_tables(self):
        result = parse_lua_table('{ ToneCurvePV2012 = { 0, 0, 64, 56, 255, 255 } }')
        self.assertEqual(result["ToneCurvePV2012"], [0, 0, 64, 56, 255, 255])
