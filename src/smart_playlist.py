"""
Smart-playlist rules: model, validation, and SQL clause builder.

A ruleset is ``{match: "all" | "any", rules: [{field, op, value}, ...]}`` and
is stored as JSON in ``playlists.smart_rules`` (NULL = regular static
playlist). The builder produces a parameterized SQL WHERE fragment that
slots into ``list_tracks_filtered``'s existing query.

Safety properties to preserve:

- Field and op identifiers are matched against whitelists. Anything outside
  the whitelist raises ``ValueError`` so it never reaches the SQL string.
- Values are always passed as parameters. The builder never interpolates
  user input into the SQL itself.
- LIKE values are escaped (``\\%`` / ``\\_``) and the LIKE clause uses
  ``ESCAPE '\\'`` so user-supplied wildcards behave as literals.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_FIELDS: dict[str, str] = {
    "artist": "t.artist",
    "album": "t.album",
    "title": "t.title",
    "tag_tier": "t.tag_tier",
    "tag_score": "t.tag_score",
    "is_liked": "t.is_liked",
}

_TEXT_OPS = {"contains", "equals", "starts_with", "ends_with"}
_NUMERIC_OPS = {"gt", "lt", "equals"}
_BOOLEAN_OPS = {"equals"}
_NUMERIC_FIELDS = {"tag_score"}
_BOOLEAN_FIELDS = {"is_liked"}
_MATCH_VALUES = {"all", "any"}

_ESCAPE = "\\"


def _is_text_field(field: str) -> bool:
    return field not in _NUMERIC_FIELDS and field not in _BOOLEAN_FIELDS


def _coerce_bool(value: Any) -> int:
    """Accept the shapes a JSON ruleset can deliver — Python bool, 0/1,
    "true"/"false" — and collapse to the int the column actually stores."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes"):
            return 1
        if s in ("false", "0", "no", ""):
            return 0
    raise ValueError(f"value {value!r} not boolean")


def _escape_like(s: str) -> str:
    # Order matters: escape the escape char first so the % / _ replacements
    # don't double-escape.
    return (
        s.replace(_ESCAPE, _ESCAPE + _ESCAPE)
        .replace("%", _ESCAPE + "%")
        .replace("_", _ESCAPE + "_")
    )


def _build_clause(field: Any, op: Any, value: Any) -> tuple[str, Any]:
    if not isinstance(field, str) or field not in _FIELDS:
        raise ValueError(f"unknown field: {field!r}")
    if not isinstance(op, str):
        raise ValueError(f"op must be a string, got {type(op).__name__}")
    col = _FIELDS[field]

    if field in _BOOLEAN_FIELDS:
        if op not in _BOOLEAN_OPS:
            raise ValueError(f"op {op!r} not valid for boolean field {field!r}")
        return f"{col} = ?", _coerce_bool(value)

    if _is_text_field(field):
        if op not in _TEXT_OPS:
            raise ValueError(f"op {op!r} not valid for text field {field!r}")
        s = "" if value is None else str(value)
        escaped = _escape_like(s)
        if op == "contains":
            return f"{col} LIKE ? ESCAPE '\\'", f"%{escaped}%"
        if op == "starts_with":
            return f"{col} LIKE ? ESCAPE '\\'", f"{escaped}%"
        if op == "ends_with":
            return f"{col} LIKE ? ESCAPE '\\'", f"%{escaped}"
        # equals — case-insensitive so "Beatles" matches "beatles".
        return f"{col} = ? COLLATE NOCASE", s

    if op not in _NUMERIC_OPS:
        raise ValueError(f"op {op!r} not valid for numeric field {field!r}")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"value {value!r} not numeric for field {field!r}")
    if op == "gt":
        return f"{col} > ?", v
    if op == "lt":
        return f"{col} < ?", v
    return f"{col} = ?", v


def coerce_ruleset(payload: Any) -> Optional[dict]:
    """Normalize and validate a ruleset payload. Returns the cleaned dict,
    or None if the input represents 'no smart logic' (None or empty rules).

    Raises ``ValueError`` on bad shape so the endpoint layer can surface a
    400 with a useful message rather than storing garbage.
    """
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("smart_rules must be an object")
    rules = payload.get("rules")
    if rules is None:
        return None
    if not isinstance(rules, list):
        raise ValueError("smart_rules.rules must be an array")
    if not rules:
        return None
    match = payload.get("match", "all")
    if match not in _MATCH_VALUES:
        raise ValueError(f"match must be 'all' or 'any', got {match!r}")

    normalized: list[dict] = []
    for r in rules:
        if not isinstance(r, dict):
            raise ValueError("each rule must be an object")
        field = r.get("field")
        op = r.get("op")
        value = r.get("value", "")
        # Validate by attempting to build; raises on unknown field/op or
        # non-numeric value for a numeric field.
        _build_clause(field, op, value)
        normalized.append({"field": field, "op": op, "value": value})
    return {"match": match, "rules": normalized}


def parse_stored(raw: Optional[str]) -> Optional[dict]:
    """Parse a smart_rules string out of the DB. Tolerant — returns None on
    blank or malformed input rather than raising, since legacy / manually-
    edited rows shouldn't crash the read path.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    try:
        return coerce_ruleset(data)
    except ValueError:
        return None


def serialize(ruleset: Optional[dict]) -> Optional[str]:
    """Inverse of parse_stored. None / empty rules → None so the column stays
    NULL rather than holding ``"{}"``.
    """
    cleaned = coerce_ruleset(ruleset)
    if cleaned is None:
        return None
    return json.dumps(cleaned, separators=(",", ":"), sort_keys=True)


def build_where(ruleset: dict) -> tuple[str, list]:
    """Compile a ruleset into ``(sql_fragment, params)``.

    The fragment is a single parenthesized expression suitable for AND-ing
    into an existing WHERE. Empty rules → ``("0", [])`` so the playlist
    resolves to zero tracks, not the full library — that's the safer
    fallback if a future caller skips coerce_ruleset.
    """
    if not ruleset:
        return ("0", [])
    rules = ruleset.get("rules") or []
    if not rules:
        return ("0", [])

    fragments: list[str] = []
    params: list = []
    for r in rules:
        frag, p = _build_clause(r.get("field"), r.get("op"), r.get("value", ""))
        fragments.append(frag)
        params.append(p)

    joiner = " OR " if ruleset.get("match") == "any" else " AND "
    return ("(" + joiner.join(fragments) + ")", params)
