import pytest
import os
import json
import tempfile
from unittest.mock import MagicMock, patch
from src.bootstrap import run_bootstrap_sequence


def test_run_bootstrap_sequence():
    """Test bootstrap sequence execution."""
    # Mock config data
    config_data = {
        'music_library_path': '/test/music',
        'downloads_path': '/test/downloads',
        'dap_mount_point': '/test/dap',
        'slsk_username': 'test_user',
        'slsk_password': 'test_pass'
    }

    with patch('src.bootstrap.get_config') as mock_get_config, \
         patch('src.bootstrap.DatabaseManager') as mock_db_manager, \
         patch('src.bootstrap.main_scan_library') as mock_scan, \
         patch('src.bootstrap.main_run_sync') as mock_sync, \
         patch('src.bootstrap.main_run_downloader') as mock_download, \
         patch('builtins.open', MagicMock()), \
         tempfile.TemporaryDirectory() as temp_dir:

        # Setup mocks
        mock_config = MagicMock()
        mock_config.db_path = ":memory:"
        mock_config.get.return_value = "/test/dap"
        mock_get_config.return_value = mock_config

        mock_db_instance = MagicMock()
        mock_db_manager.return_value.__enter__.return_value = mock_db_instance

        # Run bootstrap sequence
        run_bootstrap_sequence(config_data, run_download=False)

        # Verify calls were made
        assert mock_get_config.called
        assert mock_scan.called
        assert mock_sync.called


def test_run_bootstrap_sequence_with_download():
    """Test bootstrap sequence with download enabled."""
    # Mock config data
    config_data = {
        'music_library_path': '/test/music',
        'downloads_path': '/test/downloads',
        'dap_mount_point': '/test/dap',
        'slsk_username': 'test_user',
        'slsk_password': 'test_pass'
    }

    with patch('src.bootstrap.get_config') as mock_get_config, \
         patch('src.bootstrap.DatabaseManager') as mock_db_manager, \
         patch('src.bootstrap.main_scan_library') as mock_scan, \
         patch('src.bootstrap.main_run_sync') as mock_sync, \
         patch('src.bootstrap.main_run_downloader') as mock_download, \
         patch('builtins.open', MagicMock()), \
         tempfile.TemporaryDirectory() as temp_dir:

        # Setup mocks
        mock_config = MagicMock()
        mock_config.db_path = ":memory:"
        mock_config.get.return_value = "/test/dap"
        mock_get_config.return_value = mock_config

        mock_db_instance = MagicMock()
        mock_db_manager.return_value.__enter__.return_value = mock_db_instance

        # Run bootstrap sequence with download
        run_bootstrap_sequence(config_data, run_download=True)

        # Verify download was called
        assert mock_download.called


def test_run_bootstrap_sequence_no_dap():
    """Test bootstrap sequence without DAP mount point."""
    # Mock config data without dap mount
    config_data = {
        'music_library_path': '/test/music',
        'downloads_path': '/test/downloads',
        'slsk_username': 'test_user',
        'slsk_password': 'test_pass'
        # No dap_mount_point
    }

    with patch('src.bootstrap.get_config') as mock_get_config, \
         patch('src.bootstrap.DatabaseManager') as mock_db_manager, \
         patch('src.bootstrap.main_scan_library') as mock_scan, \
         patch('src.bootstrap.main_run_sync') as mock_sync, \
         patch('src.bootstrap.main_run_downloader') as mock_download, \
         patch('builtins.open', MagicMock()), \
         tempfile.TemporaryDirectory() as temp_dir:

        # Setup mocks
        mock_config = MagicMock()
        mock_config.db_path = ":memory:"
        mock_config.get.return_value = None  # No DAP mount point
        mock_get_config.return_value = mock_config

        mock_db_instance = MagicMock()
        mock_db_manager.return_value.__enter__.return_value = mock_db_instance

        # Run bootstrap sequence
        run_bootstrap_sequence(config_data, run_download=False)

        # Should skip sync but still do scan
        assert mock_scan.called