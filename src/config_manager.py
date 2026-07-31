"""
Centralized configuration management for DAP Manager.
"""

import json
import os
import sys
import logging
import uuid
from shutil import which
from typing import (
    Any,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Tuple,
    TypeAlias,
    cast,
)

from src.config_paths import ensure_parent_dir, resolve_config_path
from src.contracts import (
    AuthorityDeviceRole,
    ConfigData,
    ConfigMapping,
    DeviceRole,
    MutableConfigMapping,
)

logger = logging.getLogger(__name__)


DEVICE_ROLES: FrozenSet[DeviceRole] = frozenset(
    {"master", "satellite", "standalone"}
)
AUTHORITY_DEVICE_ROLES: FrozenSet[AuthorityDeviceRole] = frozenset(
    {"master", "standalone"}
)
LegacyKeyConflict: TypeAlias = Tuple[str, str]


def normalize_device_role(value: Any) -> Optional[DeviceRole]:
    """Return a supported, normalized role or ``None`` for invalid input."""
    if not isinstance(value, str):
        return None
    role = value.strip().lower()
    if role not in DEVICE_ROLES:
        return None
    return cast(DeviceRole, role)


def _legacy_is_master(value: Any) -> bool:
    """Parse the pre-``device_role`` compatibility flag conservatively."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def device_role_from_config(values: ConfigMapping) -> DeviceRole:
    """Resolve the canonical role for a raw config mapping.

    ``device_role`` is authoritative whenever it is present.  The legacy
    ``is_master`` flag is consulted only to migrate a config that predates the
    role field.  A malformed explicit role fails closed to ``satellite`` and
    must never be able to regain authority through a stale legacy boolean.
    """
    raw_role = values.get("device_role")
    role = normalize_device_role(raw_role)
    if role is not None:
        return role
    if raw_role is None or (isinstance(raw_role, str) and not raw_role.strip()):
        return "master" if _legacy_is_master(values.get("is_master")) else "satellite"
    return "satellite"


def is_authority_config(values: ConfigMapping) -> bool:
    """Whether a raw config represents a catalog-owning device."""
    return device_role_from_config(values) in AUTHORITY_DEVICE_ROLES


def sync_on_startup_from_config(values: ConfigMapping) -> bool:
    """Resolve launch-sync policy, defaulting satellites to a background pull.

    An explicit setting always wins. Older configs predate this key, so their
    role supplies the compatibility default: satellites refresh their remote
    catalogue when opened, while catalogue-owning devices remain opt-in.
    """
    configured = values.get("sync_on_startup")
    if configured is not None:
        return bool(configured)
    return device_role_from_config(values) == "satellite"


def synchronize_authority_fields(values: MutableConfigMapping) -> bool:
    """Synchronize the canonical role and its legacy serialized mirror.

    The raw ``is_master`` field remains on disk for compatibility with older
    builds, but callers must not treat it as editable or authoritative.
    Returns whether either serialized field changed.
    """
    role = device_role_from_config(values)
    legacy_value = role in AUTHORITY_DEVICE_ROLES
    changed = values.get("device_role") != role
    if changed:
        values["device_role"] = role
    if values.get("is_master") is not legacy_value:
        values["is_master"] = legacy_value
        changed = True
    return changed


def normalize_legacy_config_keys(
    values: ConfigMapping,
    legacy_key_map: Mapping[str, str],
) -> Tuple[ConfigData, bool, List[LegacyKeyConflict]]:
    """Return a normalized copy plus migration metadata.

    A legacy key is renamed when its canonical replacement is absent, and is
    removed when both names carry the same value.  Conflicting pairs are left
    untouched for the operator to resolve.  The input mapping is never
    mutated, which keeps the migration decision independently testable.
    """
    normalized = cast(ConfigData, dict(values))
    migrated = False
    conflicts: List[LegacyKeyConflict] = []

    for old, new in legacy_key_map.items():
        if old not in normalized:
            continue
        if new not in normalized:
            normalized[new] = normalized.pop(old)
            migrated = True
            continue
        if normalized[new] == normalized[old]:
            normalized.pop(old)
            migrated = True
            continue
        conflicts.append((old, new))

    return normalized, migrated, conflicts


class ConfigManager:
    """
    Singleton configuration manager for DAP Manager.
    Loads and validates configuration from config.json.
    """

    _instance: Optional["ConfigManager"] = None
    _config: ConfigData

    CONFIG_FILE: str = resolve_config_path()
    REQUIRED_KEYS: List[str] = [
        "database_file",
        "music_library_path",
        "downloads_path",
        "ffmpeg_path",
        "dap_mount_point",
        "dap_music_dir_name",
        "dap_playlist_dir_name",
    ]

    # Old keys (pre-rename) → new keys. Applied in _load_config on legacy configs.
    LEGACY_KEY_MAP: Dict[str, str] = {
        "ipod_mount_point": "dap_mount_point",
        "ipod_music_dir_name": "dap_music_dir_name",
        "ipod_playlist_dir_name": "dap_playlist_dir_name",
        # dap_manager_host_url was a parallel name for master_url used by
        # the old desktop app. Stage 9d's seeded satellites wrote both.
        "dap_manager_host_url": "master_url",
    }

    def __new__(cls) -> "ConfigManager":
        """Singleton pattern to ensure only one config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = {}
            cls._instance._load_config()
        return cls._instance

    def _load_config(self) -> None:
        """Load and validate configuration from JSON file."""
        if not os.path.exists(self.CONFIG_FILE):
            print(f"ERROR: Configuration file '{self.CONFIG_FILE}' not found.")
            print(
                "Please copy 'config.example.win.json' or 'config.example.mac.json' to 'config.json' and configure it."
            )
            sys.exit(1)

        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = cast(ConfigData, json.load(f))
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in '{self.CONFIG_FILE}': {e}")
            sys.exit(1)

        self._migrate_legacy_keys()
        self._ensure_device_identity()

        # Validate required keys
        missing = [key for key in self.REQUIRED_KEYS if key not in self._config]
        if missing:
            print(f"ERROR: Missing required configuration keys: {', '.join(missing)}")
            sys.exit(1)

        # Validate paths exist (where applicable)
        self._validate_paths()

        logger.info("Configuration loaded successfully")

    def _migrate_legacy_keys(self) -> None:
        """Rewrite pre-rename keys to their canonical names and persist."""
        normalized, migrated, conflicts = normalize_legacy_config_keys(
            self._config,
            self.LEGACY_KEY_MAP,
        )
        for old, new in conflicts:
            logger.warning(
                "Config has both '%s' and '%s' with different values; "
                "leaving as-is. Edit config.json to keep one.",
                old,
                new,
            )
        if not migrated:
            return

        self._config = normalized
        try:
            self._write_config()
            logger.info("Migrated legacy config keys")
        except OSError as e:
            logger.warning("Could not persist migrated config: %s", e)

    def _ensure_device_identity(self) -> None:
        """Generate identity and migrate/synchronize device authority fields.

        `device_id` is a UUID4 used by the host to distinguish satellites.
        ``device_role`` is canonical.  A role-less legacy config inherits
        ``master`` from ``is_master: true``; otherwise it defaults to
        ``satellite``.  ``is_master`` is then retained only as a synchronized
        compatibility mirror for older builds.
        """
        changed = False
        if not self._config.get("device_id"):
            self._config["device_id"] = str(uuid.uuid4())
            changed = True
        raw_role = self._config.get("device_role")
        if raw_role and normalize_device_role(raw_role) is None:
            logger.warning(
                "Invalid device_role %r; falling back to satellite", raw_role
            )
        changed = synchronize_authority_fields(self._config) or changed
        if not changed:
            return

        try:
            self._write_config()
            logger.info(
                "Assigned device identity: id=%s role=%s",
                self._config["device_id"],
                self._config["device_role"],
            )
        except OSError as e:
            logger.warning("Could not persist device identity: %s", e)

    def _write_config(self) -> None:
        """Persist the current JSON-shaped configuration."""
        ensure_parent_dir(self.CONFIG_FILE)
        with open(self.CONFIG_FILE, "w", encoding="utf-8") as config_file:
            json.dump(self._config, config_file, indent=4)

    def _validate_paths(self) -> None:
        """Validate that critical paths exist."""
        ffmpeg_path = cast(str, self._config["ffmpeg_path"])
        music_library_path = cast(str, self._config["music_library_path"])

        # Check ffmpeg
        if not os.path.exists(ffmpeg_path) and not which(ffmpeg_path):
            logger.warning("ffmpeg not found at: %s", ffmpeg_path)

        # Check music library
        if not os.path.exists(music_library_path):
            logger.warning("Music library path doesn't exist: %s", music_library_path)
            logger.info("Creating directory...")
            os.makedirs(music_library_path, exist_ok=True)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-style access."""
        return self._config[key]

    @property
    def db_path(self) -> str:
        return cast(str, self._config["database_file"])

    @property
    def picard_path(self) -> str:
        return cast(str, self._config.get("picard_cmd_path", ""))

    @property
    def music_library(self) -> str:
        return cast(str, self._config["music_library_path"])

    @property
    def downloads_dir(self) -> str:
        return cast(str, self._config["downloads_path"])

    @property
    def slsk_command(self) -> List[str]:
        command = self._config.get("slsk_cmd_base") or self._config.get(
            "slsk_command", []
        )
        return cast(List[str], command)

    @property
    def acoustid_api_key(self) -> str:
        return cast(str, self._config.get("acoustid_api_key", ""))

    @property
    def contact_email(self) -> str:
        return cast(str, self._config.get("contact_email", ""))

    @property
    def device_id(self) -> str:
        return cast(str, self._config.get("device_id", ""))

    @property
    def device_role(self) -> DeviceRole:
        return device_role_from_config(self._config)

    @property
    def is_master(self) -> bool:
        # Standalone owns its local catalog and therefore runs the same
        # authority-only jobs (MusicBrainz tags, Daily Mixes, Lidarr) without
        # advertising a master_url to other devices.
        return self.device_role in AUTHORITY_DEVICE_ROLES

    @property
    def report_inventory_to_host(self) -> bool:
        """Whether this device should report its MBID→path inventory.

        Explicit config wins. If unset, defaults to True on the master
        (so the master's own presence shows in the fleet view) and False
        on satellites (opt-in — quiet by default).
        """
        if "report_inventory_to_host" in self._config:
            return bool(self._config["report_inventory_to_host"])
        return self.is_master

    @property
    def master_url(self) -> str:
        """Base URL of the master DAPManager (e.g. http://host.local:5001).

        Empty on the master itself, and on satellites that haven't been
        pointed at one yet. Trailing slashes are stripped.
        """
        master_url = cast(str, self._config.get("master_url") or "")
        return master_url.rstrip("/")

    @property
    def jellyfin_enabled(self) -> bool:
        return bool(
            self._config.get("jellyfin_url")
            and self._config.get("jellyfin_api_key")
            and self._config.get("jellyfin_user_id")
        )


def get_config() -> ConfigManager:
    """Get the singleton config instance."""
    return ConfigManager()
