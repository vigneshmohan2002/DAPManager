import pytest
import os
import sys
import tempfile
import json
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.db_manager import DatabaseManager, Track, Playlist
from web_server import app, TaskManager

@pytest.fixture
def db():
    """Returns an in-memory database manager."""
    # Use :memory: for fast, isolated testing
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
    
    # Patch the config in web_server module if needed, 
    # but since we rely on dependency injection or global state, 
    # we might need to patch web_server.config directly.
    import web_server
    monkeypatch.setattr(web_server, 'config', mock)
    
    # Also patch TaskManager to avoid actual threads
    monkeypatch.setattr(web_server, 'task_manager', TaskManager())
    
    return mock
