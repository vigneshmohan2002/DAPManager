import json

import pytest

from src.smart_playlist import (
    build_where,
    coerce_ruleset,
    parse_stored,
    serialize,
)


# --- coerce_ruleset ---------------------------------------------------------

def test_coerce_returns_none_for_empty_inputs():
    assert coerce_ruleset(None) is None
    assert coerce_ruleset({"rules": []}) is None
    assert coerce_ruleset({"rules": None}) is None


def test_coerce_validates_match_combinator():
    with pytest.raises(ValueError, match="match must be"):
        coerce_ruleset({"match": "xor", "rules": [
            {"field": "artist", "op": "contains", "value": "a"}
        ]})


def test_coerce_defaults_match_to_all():
    out = coerce_ruleset({"rules": [
        {"field": "artist", "op": "contains", "value": "Beatles"}
    ]})
    assert out == {
        "match": "all",
        "rules": [{"field": "artist", "op": "contains", "value": "Beatles"}],
    }


def test_coerce_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        coerce_ruleset({"rules": [
            {"field": "rating", "op": "equals", "value": 5}
        ]})


def test_coerce_rejects_unknown_op_for_text():
    with pytest.raises(ValueError, match="not valid for text field"):
        coerce_ruleset({"rules": [
            {"field": "artist", "op": "gt", "value": "M"}
        ]})


def test_coerce_rejects_unknown_op_for_numeric():
    with pytest.raises(ValueError, match="not valid for numeric field"):
        coerce_ruleset({"rules": [
            {"field": "tag_score", "op": "contains", "value": 0.5}
        ]})


def test_coerce_rejects_non_numeric_value_for_numeric_field():
    with pytest.raises(ValueError, match="not numeric"):
        coerce_ruleset({"rules": [
            {"field": "tag_score", "op": "gt", "value": "high"}
        ]})


def test_coerce_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be an object"):
        coerce_ruleset("not a dict")


def test_coerce_rejects_non_object_rule():
    with pytest.raises(ValueError, match="rule must be an object"):
        coerce_ruleset({"rules": ["broken"]})


# --- build_where -----------------------------------------------------------

def test_build_where_empty_resolves_to_zero():
    sql, params = build_where({"match": "all", "rules": []})
    assert sql == "0"
    assert params == []


def test_build_where_text_contains_uses_like_with_escape():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "artist", "op": "contains", "value": "Beat"}
    ]})
    assert "t.artist LIKE ? ESCAPE '\\'" in sql
    assert params == ["%Beat%"]


def test_build_where_text_starts_and_ends():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "title", "op": "starts_with", "value": "Help"},
        {"field": "title", "op": "ends_with", "value": "Live"},
    ]})
    assert params == ["Help%", "%Live"]
    # Both clauses joined with AND for match=all.
    assert " AND " in sql


def test_build_where_text_equals_is_case_insensitive():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "tag_tier", "op": "equals", "value": "GREEN"}
    ]})
    assert "COLLATE NOCASE" in sql
    assert params == ["GREEN"]


def test_build_where_numeric_gt_lt():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "tag_score", "op": "gt", "value": 0.8},
        {"field": "tag_score", "op": "lt", "value": 1.0},
    ]})
    assert "t.tag_score > ?" in sql
    assert "t.tag_score < ?" in sql
    assert params == [0.8, 1.0]


def test_build_where_match_any_joins_with_or():
    sql, params = build_where({"match": "any", "rules": [
        {"field": "artist", "op": "contains", "value": "Beatles"},
        {"field": "artist", "op": "contains", "value": "Stones"},
    ]})
    assert " OR " in sql
    assert " AND " not in sql
    assert params == ["%Beatles%", "%Stones%"]


def test_build_where_escapes_user_wildcards():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "title", "op": "contains", "value": "100% live"}
    ]})
    # The user's literal "%" must be escaped so SQLite treats it as a literal.
    assert params == ["%100\\% live%"]
    assert "ESCAPE '\\'" in sql


def test_build_where_handles_underscore_wildcard():
    sql, params = build_where({"match": "all", "rules": [
        {"field": "title", "op": "starts_with", "value": "track_01"}
    ]})
    assert params == ["track\\_01%"]


# --- serialize / parse_stored round-trip -----------------------------------

def test_serialize_normalizes_and_roundtrips():
    raw = serialize({"match": "any", "rules": [
        {"field": "artist", "op": "contains", "value": "Beatles"}
    ]})
    assert isinstance(raw, str)
    parsed = parse_stored(raw)
    assert parsed == {
        "match": "any",
        "rules": [{"field": "artist", "op": "contains", "value": "Beatles"}],
    }


def test_serialize_returns_none_for_empty_ruleset():
    assert serialize(None) is None
    assert serialize({"rules": []}) is None


def test_parse_stored_tolerates_garbage():
    # A future migration / manual edit shouldn't crash the read path.
    assert parse_stored(None) is None
    assert parse_stored("") is None
    assert parse_stored("not json") is None
    assert parse_stored("[1, 2, 3]") is None  # Valid JSON, wrong shape.
    # Valid JSON, valid shape, but unknown field — tolerated as None.
    assert parse_stored(json.dumps({"rules": [
        {"field": "rating", "op": "equals", "value": 5}
    ]})) is None
