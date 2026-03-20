import pytest
import os
import json
import tempfile
from unittest.mock import patch, MagicMock
from src.config_manager import ConfigManager, get_config


def test_config_manager_singleton():
    """Test that ConfigManager is a singleton."""
    # Reset singleton instance for testing
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        music_path = os.path.join(temp_dir, "music")
        downloads_path = os.path.join(temp_dir, "downloads")
        ipod_path = os.path.join(temp_dir, "ipod")

        config_data = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "ipod_mount_point": ipod_path,
            "ipod_music_dir_name": "Music",
            "ipod_playlist_dir_name": "Playlists"
        }

        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        # Temporarily change the config file path
        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            # Get config instance
            config1 = get_config()
            config2 = get_config()

            # Should be the same instance
            assert config1 is config2
        finally:
            # Restore original config file path
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_config_manager_missing_file():
    """Test ConfigManager with missing config file."""
    # Reset singleton instance for testing
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "nonexistent.json")

        # Temporarily change the config file path
        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            # Should raise SystemExit
            with pytest.raises(SystemExit):
                get_config()
        finally:
            # Restore original config file path
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_config_manager_invalid_json():
    """Test ConfigManager with invalid JSON."""
    # Reset singleton instance for testing
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        # Write invalid JSON
        with open(config_file, 'w') as f:
            f.write("invalid json")

        # Temporarily change the config file path
        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            # Should raise SystemExit
            with pytest.raises(SystemExit):
                get_config()
        finally:
            # Restore original config file path
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_config_manager_missing_required_keys():
    """Test ConfigManager with missing required keys."""
    # Reset singleton instance for testing
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        # Write config with missing keys
        config_data = {
            "database_file": "test.db"
            # Missing other required keys
        }

        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        # Temporarily change the config file path
        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            # Should raise SystemExit
            with pytest.raises(SystemExit):
                get_config()
        finally:
            # Restore original config file path
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_config_manager_get_method():
    """Test ConfigManager get method."""
    # Reset singleton instance for testing
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        music_path = os.path.join(temp_dir, "music")
        downloads_path = os.path.join(temp_dir, "downloads")
        ipod_path = os.path.join(temp_dir, "ipod")

        config_data = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "ipod_mount_point": ipod_path,
            "ipod_music_dir_name": "Music",
            "ipod_playlist_dir_name": "Playlists",
            "some_optional_key": "optional_value"
        }

        with open(config_file, 'w') as f:
            json.dump(config_data, f)

        # Temporarily change the config file path
        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            config = get_config()

            # Test get method with existing key
            assert config.get("database_file") == "test.db"

            # Test get method with default value
            assert config.get("nonexistent_key", "default") == "default"

            # Test dictionary-style access
            assert config["database_file"] == "test.db"

            # Test property access
            assert config.db_path == "test.db"
        finally:
            # Restore original config file path
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None