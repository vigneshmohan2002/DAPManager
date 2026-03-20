import pytest
import os
import sys
import tempfile
import json
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db_manager import DatabaseManager, Track, Playlist

# web_server.py calls init_app_logic() at module level if config.json exists,
# which loads the real config with hardcoded paths. We prevent this by patching
# config_exists to return False during the import.
import unittest.mock as _mock
_patcher = _mock.patch.dict(os.environ, {"_DAP_TESTING": "1"})
_patcher.start()

# Temporarily make config_exists() return False at import time
# by ensuring neither config file is found
_real_exists = os.path.exists

def _no_config_exists(p):
    if isinstance(p, str) and os.path.basename(p) in ("config.json", "config-mac.json"):
        return False
    return _real_exists(p)

_exists_patcher = _mock.patch("os.path.exists", side_effect=_no_config_exists)
_exists_patcher.start()

try:
    import web_server
    from web_server import app, TaskManager
finally:
    _exists_patcher.stop()
    _patcher.stop()

# init_app_logic() was skipped, so set the module-level names that route
# handlers reference.  Tests will patch these as needed.
from src.db_manager import DatabaseManager as _Dm, DownloadItem as _Di
web_server.DatabaseManager = _Dm
web_server.DownloadItem = _Di


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    """Reset the ConfigManager singleton between tests."""
    from src.config_manager import ConfigManager
    ConfigManager._instance = None
    yield
    ConfigManager._instance = None


@pytest.fixture
def db():
    """Returns an in-memory database manager."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def client():
    """Returns a Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_config(monkeypatch):
    """Mocks the global config object in web_server logic."""
    mock = MagicMock()
    mock.db_path = ":memory:"
    mock.music_library = "/tmp/music"

    monkeypatch.setattr(web_server, 'config', mock)
    monkeypatch.setattr(web_server, 'task_manager', TaskManager())

    return mock
