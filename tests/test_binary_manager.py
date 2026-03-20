import pytest
import os
import tempfile
import platform
from unittest.mock import patch
from src.binary_manager import detect_platform, install_from_local


def test_detect_platform_macos():
    """Test platform detection for macOS."""
    with patch('platform.system') as mock_system, \
         patch('platform.machine') as mock_machine:
        mock_system.return_value = 'Darwin'
        mock_machine.return_value = 'arm64'

        os_name, arch_name = detect_platform()
        assert os_name == 'osx'
        assert arch_name == 'arm64'


def test_detect_platform_linux():
    """Test platform detection for Linux."""
    with patch('platform.system') as mock_system, \
         patch('platform.machine') as mock_machine:
        mock_system.return_value = 'Linux'
        mock_machine.return_value = 'x86_64'

        os_name, arch_name = detect_platform()
        assert os_name == 'linux'
        assert arch_name == 'x64'


def test_detect_platform_windows():
    """Test platform detection for Windows."""
    with patch('platform.system') as mock_system, \
         patch('platform.machine') as mock_machine:
        mock_system.return_value = 'Windows'
        mock_machine.return_value = 'AMD64'

        os_name, arch_name = detect_platform()
        assert os_name == 'win'
        assert arch_name == 'x86'  # Always x86 for Windows


def test_detect_platform_unsupported():
    """Test platform detection for unsupported OS."""
    with patch('platform.system') as mock_system:
        mock_system.return_value = 'UnsupportedOS'

        with pytest.raises(Exception, match="Unsupported OS"):
            detect_platform()


def test_install_from_local_file_not_found():
    """Test install from local with missing file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        releases_dir = os.path.join(temp_dir, "releases")
        bin_dir = os.path.join(temp_dir, "bin")
        os.makedirs(releases_dir, exist_ok=True)
        os.makedirs(bin_dir, exist_ok=True)

        # Test with non-existent zip file
        with pytest.raises(FileNotFoundError):
            install_from_local(releases_dir, bin_dir)