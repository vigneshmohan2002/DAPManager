import pytest
import json
from unittest.mock import patch, MagicMock
from src.db_manager import DatabaseManager

def test_api_status(client, mock_config):
    """Test the status endpoint returns correct structure."""
    res = client.get('/api/status')
    assert res.status_code == 200
    data = res.get_json()
    assert 'running' in data
    assert 'message' in data
    assert 'detail' in data

def test_api_stats(client, mock_config):
    """Test stats endpoint. Mocks DB interaction."""
    # We need to mock DatabaseManager within web_server context
    # or ensure client uses the mocked config which points to :memory:?
    # The 'run_audit' and others create their own DatabaseManager(config.db_path).
    # Since mock_config.db_path is :memory:, it creates a FRESH empty DB every time 
    # unless we patch DatabaseManager to return a shared mock.
    
    # Easier: Patch verify the call works and handles empty DB gracefully.
    
    with patch('web_server.DatabaseManager') as MockDB:
        mock_instance = MockDB.return_value.__enter__.return_value
        mock_instance.get_library_stats.return_value = {
            'tracks': 100, 'artists': 10, 'albums': 5, 'playlists': 2, 'incomplete_albums': 1
        }
        
        # Also mock shutil because disk usage is real system call
        with patch('shutil.disk_usage') as mock_du:
            mock_du.return_value = (100*1024**3, 50*1024**3, 50*1024**3) # 100GB total, 50 used, 50 free
            
            res = client.get('/api/stats')
            assert res.status_code == 200
            data = res.get_json()
            assert data['success'] is True
            assert data['stats']['tracks'] == 100
            assert data['stats']['disk_free_gb'] == 50.0

def test_search_api(client, mock_config):
    """Test search endpoint."""
    with patch('web_server.DatabaseManager') as MockDB:
        mock_instance = MockDB.return_value.__enter__.return_value
        # Mock search results
        mock_t = MagicMock()
        mock_t.artist = "Foo"
        mock_t.title = "Bar"
        mock_t.album = "Baz"
        mock_t.local_path = "/path"
        mock_t.mbid = "123"
        
        mock_instance.search_tracks.return_value = [mock_t]
        
        res = client.get('/api/library/search?q=Foo')
        assert res.status_code == 200
        data = res.get_json()
        assert data['success'] is True
        assert len(data['results']) == 1
        assert data['results'][0]['artist'] == "Foo"
