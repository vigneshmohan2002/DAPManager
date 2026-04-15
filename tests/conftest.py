import pytest
import os
import sys
import tempfile
import json
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db_manager import DatabaseManager, Track, Playlist, DownloadItem

# web_server import is now side-effect-free (init_app_logic is only called
# from `if __name__ == "__main__"` or before_request).
import web_server
from web_server import app, TaskManager

# Route handlers reference DatabaseManager / DownloadItem as module-level
# names populated by init_app_logic(). Set them up directly for tests.
web_server.DatabaseManager = DatabaseManager
web_server.DownloadItem = DownloadItem


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    """Reset the ConfigManager singleton between tests."""
    from src.config_manager import ConfigManager
    ConfigManager._instance = None
    yield
    ConfigManager._instance = None


@pytest.fixture(autouse=True)
def _disable_mb_rate_limit():
    """Disable MusicBrainz rate limiting so the test suite stays fast."""
    from src import musicbrainz_client
    original = musicbrainz_client._MIN_INTERVAL_SEC
    musicbrainz_client._MIN_INTERVAL_SEC = 0
    musicbrainz_client.reset_for_tests()
    yield
    musicbrainz_client._MIN_INTERVAL_SEC = original
    musicbrainz_client.reset_for_tests()


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
