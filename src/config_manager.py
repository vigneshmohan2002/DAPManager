"""
Centralized configuration management for DAP Manager.
"""

import json
import os
import sys
import logging
from typing import Dict, Any, Optional
from shutil import which

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Singleton configuration manager for DAP Manager.
    Loads and validates configuration from config.json.
    """

    _instance: Optional["ConfigManager"] = None
    _config: Dict[str, Any] = {}

    CONFIG_FILE = "config.json"
    REQUIRED_KEYS = [
        "database_file",
        "picard_cmd_path",
        "music_library_path",
        "downloads_path",
        "ffmpeg_path",
        "ipod_mount_point",
        "ipod_music_dir_name",
        "ipod_playlist_dir_name",
    ]

    def __new__(cls):
        """Singleton pattern to ensure only one config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """Load and validate configuration from JSON file."""
        if not os.path.exists(self.CONFIG_FILE):
            print(f"ERROR: Configuration file '{self.CONFIG_FILE}' not found.")
            print(
                "Please copy 'config.json.template' to 'config.json' and configure it."
            )
            sys.exit(1)

        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in '{self.CONFIG_FILE}': {e}")
            sys.exit(1)

        # Validate required keys
        missing = [key for key in self.REQUIRED_KEYS if key not in self._config]
        if missing:
            print(f"ERROR: Missing required configuration keys: {', '.join(missing)}")
            sys.exit(1)

        # Validate paths exist (where applicable)
        self._validate_paths()

        logger.info("Configuration loaded successfully")

    def _validate_paths(self):
        """Validate that critical paths exist."""
        # Check Picard
        if not os.path.exists(self._config["picard_cmd_path"]):
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
        return self._config.get("slsk_cmd_base", [])

    @property
    def acoustid_api_key(self) -> str:
        return self._config.get("acoustid_api_key", "")


def get_config() -> ConfigManager:
    """Get the singleton config instance."""
    return ConfigManager()
