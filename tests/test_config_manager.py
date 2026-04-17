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
        dap_path = os.path.join(temp_dir, "dap")

        config_data = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "dap_mount_point": dap_path,
            "dap_music_dir_name": "Music",
            "dap_playlist_dir_name": "Playlists"
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


def test_config_legacy_ipod_keys_migrate_to_dap():
    """Legacy ipod_* keys should be renamed to dap_* and persisted to disk."""
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        music_path = os.path.join(temp_dir, "music")
        downloads_path = os.path.join(temp_dir, "downloads")
        ipod_path = os.path.join(temp_dir, "ipod")

        legacy_config = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "ipod_mount_point": ipod_path,
            "ipod_music_dir_name": "Music",
            "ipod_playlist_dir_name": "Playlists",
        }

        with open(config_file, "w") as f:
            json.dump(legacy_config, f)

        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file

        try:
            config = get_config()

            # Legacy keys gone, new keys present in the loaded dict.
            assert config.get("ipod_mount_point") is None
            assert config.get("dap_mount_point") == ipod_path
            assert config.get("dap_music_dir_name") == "Music"
            assert config.get("dap_playlist_dir_name") == "Playlists"

            # Migration rewrote the file on disk.
            with open(config_file, "r") as f:
                persisted = json.load(f)
            assert "ipod_mount_point" not in persisted
            assert persisted["dap_mount_point"] == ipod_path
        finally:
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_jellyfin_enabled_property():
    """jellyfin_enabled is True iff url, api_key, and user_id are all set."""
    ConfigManager._instance = None

    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        music_path = os.path.join(temp_dir, "music")
        downloads_path = os.path.join(temp_dir, "downloads")
        dap_path = os.path.join(temp_dir, "dap")

        base = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "dap_mount_point": dap_path,
            "dap_music_dir_name": "Music",
            "dap_playlist_dir_name": "Playlists",
        }

        # No Jellyfin keys → disabled.
        with open(config_file, "w") as f:
            json.dump(base, f)

        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file
        try:
            assert get_config().jellyfin_enabled is False
        finally:
            ConfigManager._instance = None

        # All three keys set → enabled.
        with_jf = {
            **base,
            "jellyfin_url": "http://jf.local:8096",
            "jellyfin_api_key": "secret",
            "jellyfin_user_id": "uid-123",
        }
        with open(config_file, "w") as f:
            json.dump(with_jf, f)

        try:
            assert get_config().jellyfin_enabled is True
        finally:
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
        dap_path = os.path.join(temp_dir, "dap")

        config_data = {
            "database_file": "test.db",
            "picard_cmd_path": "picard",
            "music_library_path": music_path,
            "downloads_path": downloads_path,
            "ffmpeg_path": "ffmpeg",
            "dap_mount_point": dap_path,
            "dap_music_dir_name": "Music",
            "dap_playlist_dir_name": "Playlists",
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


def _write_minimal_config(path, overrides=None):
    """Write a minimal valid config.json; returns the written dict."""
    tmp = os.path.dirname(path)
    data = {
        "database_file": "test.db",
        "picard_cmd_path": "picard",
        "music_library_path": os.path.join(tmp, "music"),
        "downloads_path": os.path.join(tmp, "downloads"),
        "ffmpeg_path": "ffmpeg",
        "dap_mount_point": os.path.join(tmp, "dap"),
        "dap_music_dir_name": "Music",
        "dap_playlist_dir_name": "Playlists",
    }
    if overrides:
        data.update(overrides)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def test_device_identity_generated_on_first_run():
    ConfigManager._instance = None
    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        _write_minimal_config(config_file)

        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file
        try:
            config = get_config()
            assert config.device_id  # non-empty UUID
            assert len(config.device_id) == 36  # canonical UUID string length
            assert config.device_role == "satellite"
            assert config.is_master is False

            # Persisted back to disk
            with open(config_file) as f:
                on_disk = json.load(f)
            assert on_disk["device_id"] == config.device_id
            assert on_disk["device_role"] == "satellite"
        finally:
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None


def test_device_identity_preserved_between_loads():
    ConfigManager._instance = None
    with tempfile.TemporaryDirectory() as temp_dir:
        config_file = os.path.join(temp_dir, "config.json")
        _write_minimal_config(
            config_file,
            overrides={"device_id": "fixed-uuid-123", "device_role": "master"},
        )

        original_file = ConfigManager.CONFIG_FILE
        ConfigManager.CONFIG_FILE = config_file
        try:
            config = get_config()
            assert config.device_id == "fixed-uuid-123"
            assert config.device_role == "master"
            assert config.is_master is True
        finally:
            ConfigManager.CONFIG_FILE = original_file
            ConfigManager._instance = None