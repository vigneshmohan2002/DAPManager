"""Configuration persistence and response shaping for HTTP adapters."""

import json
import os
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Tuple

from src.config_keys import (
    BOOL_KEYS,
    DEFAULT_VALUES,
    EDITABLE_KEYS,
    GROUPS,
    SECRET_KEYS,
)
from src.config_manager import (
    ConfigManager,
    normalize_device_role,
    synchronize_authority_fields,
)
from src.config_paths import ensure_parent_dir


FIRST_RUN_FIELDS = frozenset({
    "music_library_path", "downloads_path", "dap_mount_point",
    "master_url", "public_master_url", "api_token", "device_name",
    "slsk_username", "slsk_password",
    "jellyfin_url", "jellyfin_api_key", "jellyfin_user_id",
    "lidarr_url", "lidarr_api_key", "lidarr_enabled",
    "acoustid_api_key", "contact_email",
    "report_inventory_to_host", "contribute_to_host",
    "fast_search", "remove_ft", "desperate_mode", "strict_quality",
})

SYNC_SCHEDULER_KEYS = frozenset({
    "sync_interval_seconds",
    "sync_on_startup",
})
RELEASE_WATCHER_KEYS = frozenset({
    "lidarr_watch_enabled",
    "lidarr_watch_interval_seconds",
    "device_role",
})
LIBRARY_MAINTENANCE_KEYS = frozenset({
    "library_maintenance_interval_seconds",
    "library_maintenance_on_startup",
    "device_role",
})


class InitialConfigBuilder(Protocol):
    """Callable shape of ``first_run.build_initial_config``."""

    def __call__(
        self,
        role: str,
        **values: Any,
    ) -> Dict[str, Any]: ...


class RuntimeConfig(Protocol):
    """Runtime configuration operation used after a settings write."""

    def _load_config(self) -> None: ...


class StartupAwareRestart(Protocol):
    """Scheduler restart accepting the established startup override."""

    def __call__(self, *, run_on_startup: bool) -> Any: ...


def read_config_file(path: str) -> Dict[str, Any]:
    """Read the existing JSON object without changing its wire values."""
    with open(path, "r") as config_file:
        return json.load(config_file)


def write_config_file(path: str, values: Mapping[str, Any]) -> None:
    """Persist configuration using the established four-space JSON format."""
    ensure_parent_dir(path)
    with open(path, "w") as config_file:
        json.dump(values, config_file, indent=4)


def build_first_run_config(
    path: str,
    data: Mapping[str, Any],
    builder: InitialConfigBuilder,
) -> Dict[str, Any]:
    """Build the canonical setup payload without trusting client-owned paths."""
    role = (data.get("role") or "master").strip().lower()
    payload = {
        key: value
        for key, value in data.items()
        if key in FIRST_RUN_FIELDS
    }
    database_file = os.path.join(
        os.path.dirname(os.path.abspath(path)),
        "dap_library.db",
    )
    config_values = builder(
        role,
        database_file=database_file,
        **payload,
    )
    synchronize_authority_fields(config_values)
    return config_values


def reload_runtime_config(
    runtime_config: RuntimeConfig | None,
    changed: Iterable[str],
    *,
    start_sync_scheduler: StartupAwareRestart,
    start_release_watcher: Callable[[], Any],
    start_library_maintenance_scheduler: StartupAwareRestart,
) -> None:
    """Reload process state and restart only schedulers affected by a write."""
    if runtime_config is not None:
        runtime_config._load_config()
        if isinstance(runtime_config, ConfigManager):
            ConfigManager._instance = runtime_config

    changed_keys = frozenset(changed)
    if changed_keys & SYNC_SCHEDULER_KEYS:
        start_sync_scheduler(run_on_startup=False)
    if changed_keys & RELEASE_WATCHER_KEYS:
        start_release_watcher()
    if changed_keys & LIBRARY_MAINTENANCE_KEYS:
        start_library_maintenance_scheduler(run_on_startup=False)


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
