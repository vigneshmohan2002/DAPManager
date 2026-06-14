"""Unit tests for src/smart_playlist.py validation + (de)serialization."""

import json
import unittest

from src import smart_playlist as sp


class TestSerialize(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(sp.serialize(None))

    def test_valid_boolean_rule(self):
        out = sp.serialize({"match": "all",
                            "rules": [{"field": "is_liked", "op": "equals", "value": True}]})
        self.assertIsInstance(out, str)
        self.assertEqual(json.loads(out)["match"], "all")

    def test_valid_text_rule(self):
        out = sp.serialize({"match": "any",
                            "rules": [{"field": "artist", "op": "contains", "value": "Kendrick"}]})
        self.assertIn("artist", out)

    def test_valid_numeric_rule(self):
        out = sp.serialize({"match": "all",
                            "rules": [{"field": "tag_score", "op": "gt", "value": 0.9}]})
        self.assertIn("tag_score", out)

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            sp.serialize({"match": "all",
                          "rules": [{"field": "definitely_not_a_field", "op": "equals", "value": 1}]})

    def test_wrong_op_for_boolean_rejected(self):
        with self.assertRaises(ValueError):
            sp.serialize({"match": "all",
                          "rules": [{"field": "is_liked", "op": "contains", "value": True}]})


class TestRoundTrip(unittest.TestCase):
    def test_serialize_then_parse(self):
        ruleset = {"match": "all",
                   "rules": [{"field": "artist", "op": "equals", "value": "SZA"}]}
        stored = sp.serialize(ruleset)
        parsed = sp.parse_stored(stored)
        self.assertEqual(parsed["match"], "all")
        self.assertEqual(parsed["rules"][0]["field"], "artist")

    def test_parse_none_is_none(self):
        self.assertIsNone(sp.parse_stored(None))


class TestBuildWhere(unittest.TestCase):
    def test_produces_parameterized_clause(self):
        ruleset = {"match": "all",
                   "rules": [{"field": "artist", "op": "contains", "value": "Drake"}]}
        clause, params = sp.build_where(ruleset)
        self.assertIsInstance(clause, str)
        self.assertIn("?", clause)            # parameterized, not interpolated
        self.assertIn("Drake", str(params))   # value travels as a bound param
        self.assertNotIn("Drake", clause)     # ...never in the SQL string


if __name__ == "__main__":
    unittest.main(verbosity=2)
