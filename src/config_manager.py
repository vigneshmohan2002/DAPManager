"""
Centralized configuration management for DAP Manager.
"""

import json
import os
import sys
import logging
import uuid
from typing import Dict, Any, Optional
from shutil import which

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Singleton configuration manager for DAP Manager.
    Loads and validates configuration from config.json.
    """

    _instance: Optional["ConfigManager"] = None

    CONFIG_FILE = "config.json"
    REQUIRED_KEYS = [
        "database_file",
        "picard_cmd_path",
        "music_library_path",
        "downloads_path",
        "ffmpeg_path",
        "dap_mount_point",
        "dap_music_dir_name",
        "dap_playlist_dir_name",
    ]

    # Old keys (pre-rename) → new keys. Applied in _load_config on legacy configs.
    LEGACY_KEY_MAP = {
        "ipod_mount_point": "dap_mount_point",
        "ipod_music_dir_name": "dap_music_dir_name",
        "ipod_playlist_dir_name": "dap_playlist_dir_name",
    }

    def __new__(cls):
        """Singleton pattern to ensure only one config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = {}
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Load and validate configuration from JSON file."""
        if not os.path.exists(self.CONFIG_FILE):
            print(f"ERROR: Configuration file '{self.CONFIG_FILE}' not found.")
            print(
                "Please copy 'config.example.win.json' or 'config.example.mac.json' to 'config.json' and configure it."
            )
            sys.exit(1)

        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = json.load(f)
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

    def _migrate_legacy_keys(self):
        """Rewrite pre-rename ipod_* keys to dap_* and persist the update."""
        migrated = False
        for old, new in self.LEGACY_KEY_MAP.items():
            if old in self._config and new not in self._config:
                self._config[new] = self._config.pop(old)
                migrated = True
        if migrated:
            try:
                with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, indent=4)
                logger.info("Migrated legacy ipod_* config keys to dap_*")
            except OSError as e:
                logger.warning(f"Could not persist migrated config: {e}")

    def _ensure_device_identity(self):
        """Generate a stable device_id on first run and default device_role.

        `device_id` is a UUID4 used by the host to distinguish satellites.
        `device_role` defaults to 'satellite'; the Jellyfin host's install
        should flip this to 'master' manually in config.json.
        """
        changed = False
        if not self._config.get("device_id"):
            self._config["device_id"] = str(uuid.uuid4())
            changed = True
        if not self._config.get("device_role"):
            self._config["device_role"] = "satellite"
            changed = True
        if changed:
            try:
                with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, indent=4)
                logger.info(
                    "Assigned device identity: id=%s role=%s",
                    self._config["device_id"],
                    self._config["device_role"],
                )
            except OSError as e:
                logger.warning("Could not persist device identity: %s", e)

    def _validate_paths(self):
        """Validate that critical paths exist."""
        # Check Picard
        if not os.path.exists(self._config["picard_cmd_path"]) and not which(
            self._config["picard_cmd_path"]
        ):
            logger.warning(f"Picard not found at: {self._config['picard_cmd_path']}")

        # Check ffmpeg
        if not os.path.exists(self._config["ffmpeg_path"]) and not which(
            self._config["ffmpeg_path"]
        ):
            logger.warning(f"ffmpeg not found at: {self._config['ffmpeg_path']}")

        # Check music library
        if not os.path.exists(self._config["music_library_path"]):
            logger.warning(
                f"Music library path doesn't exist: {self._config['music_library_path']}"
            )
            logger.info("Creating directory...")
            os.makedirs(self._config["music_library_path"], exist_ok=True)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-style access."""
        return self._config[key]

    @property
    def db_path(self) -> str:
        return self._config["database_file"]

    @property
    def picard_path(self) -> str:
        return self._config["picard_cmd_path"]

    @property
    def music_library(self) -> str:
        return self._config["music_library_path"]

    @property
    def downloads_dir(self) -> str:
        return self._config["downloads_path"]

    @property
    def slsk_command(self) -> list:
        return self._config.get("slsk_cmd_base") or self._config.get("slsk_command", [])

    @property
    def acoustid_api_key(self) -> str:
        return self._config.get("acoustid_api_key", "")

    @property
    def contact_email(self) -> str:
        return self._config.get("contact_email", "")

    @property
    def device_id(self) -> str:
        return self._config.get("device_id", "")

    @property
    def device_role(self) -> str:
        return self._config.get("device_role", "satellite")

    @property
    def is_master(self) -> bool:
        return self.device_role == "master"

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
