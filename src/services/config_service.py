"""Configuration persistence and response shaping for HTTP adapters."""

import json
from typing import Any, Dict, List, Mapping, Tuple

from src.config_keys import (
    BOOL_KEYS,
    DEFAULT_VALUES,
    EDITABLE_KEYS,
    GROUPS,
    SECRET_KEYS,
)
from src.config_manager import normalize_device_role, synchronize_authority_fields
from src.config_paths import ensure_parent_dir


def read_config_file(path: str) -> Dict[str, Any]:
    """Read the existing JSON object without changing its wire values."""
    with open(path, "r") as config_file:
        return json.load(config_file)


def write_config_file(path: str, values: Mapping[str, Any]) -> None:
    """Persist configuration using the established four-space JSON format."""
    ensure_parent_dir(path)
    with open(path, "w") as config_file:
        json.dump(values, config_file, indent=4)


def build_public_config(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the stable Settings payload with configured secrets redacted."""
    redacted = {**DEFAULT_VALUES, **raw}
    synchronize_authority_fields(redacted)
    for key in SECRET_KEYS:
        if key in redacted and redacted[key]:
            redacted[key] = ""

    return {
        "success": True,
        "config": redacted,
        "editable_keys": sorted(EDITABLE_KEYS),
        "secret_keys": sorted(SECRET_KEYS),
        "bool_keys": sorted(BOOL_KEYS),
        "groups": [
            {"label": label, "keys": keys}
            for label, keys in GROUPS
        ],
    }


def normalize_config_update(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize role input or raise the route's existing validation error."""
    normalized = dict(data)
    if "device_role" not in normalized:
        return normalized

    role = normalize_device_role(normalized["device_role"])
    if role is None:
        raise ValueError(
            "device_role must be master, satellite, or standalone"
        )
    normalized["device_role"] = role
    return normalized


def merge_config_update(
    current: Mapping[str, Any],
    data: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Apply the editable-key and blank-secret rules to a copied mapping."""
    merged = dict(current)
    synchronize_authority_fields(merged)
    changed: List[str] = []

    for key, value in data.items():
        if key not in EDITABLE_KEYS:
            continue
        if key in SECRET_KEYS and value == "":
            continue
        if merged.get(key) == value:
            continue
        merged[key] = value
        changed.append(key)

    synchronize_authority_fields(merged)
    return merged, changed
