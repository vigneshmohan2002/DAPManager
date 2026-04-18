import pytest
import json
from unittest.mock import patch, MagicMock
from src.db_manager import DatabaseManager
from web_server import build_suggestion_items

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


# ---------------------------------------------------------------------------
# build_suggestion_items
# ---------------------------------------------------------------------------

def test_build_suggestion_items_prefers_search_query():
    pairs = build_suggestion_items([{"search_query": "Radiohead - Idioteque", "mbid": "abc"}])
    assert pairs == [("Radiohead - Idioteque", "abc")]


def test_build_suggestion_items_joins_artist_and_title():
    pairs = build_suggestion_items([{"artist": "Radiohead", "title": "Idioteque"}])
    assert pairs == [("Radiohead - Idioteque", "")]


def test_build_suggestion_items_skips_invalid_entries():
    pairs = build_suggestion_items([
        {},
        {"artist": "A"},          # title missing
        "not a dict",
        {"title": "Lonely Song"}, # title-only is acceptable
        {"search_query": "  "},   # whitespace-only
    ])
    assert pairs == [("Lonely Song", "")]


def test_build_suggestion_items_dedupes_case_insensitively():
    pairs = build_suggestion_items([
        {"search_query": "Foo - Bar"},
        {"search_query": "foo - bar"},
        {"artist": "FOO", "title": "BAR"},
    ])
    assert pairs == [("Foo - Bar", "")]


# ---------------------------------------------------------------------------
# /api/suggestions
# ---------------------------------------------------------------------------

def test_post_suggestions_queues_new_items(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = False

        res = client.post('/api/suggestions', json={
            "items": [
                {"artist": "Radiohead", "title": "Idioteque", "mbid": "mb1"},
                {"search_query": "Portishead - Roads"},
            ]
        })

    assert res.status_code == 200
    data = res.get_json()
    assert data == {
        "success": True,
        "received": 2,
        "queued": 2,
        "skipped": 0,
    }
    assert mock_db.queue_download.call_count == 2
    queued_item = mock_db.queue_download.call_args_list[0].args[0]
    assert queued_item.search_query == "Radiohead - Idioteque"
    assert queued_item.playlist_id == "SUGGESTED"
    assert queued_item.mbid_guess == "mb1"


def test_post_suggestions_skips_already_queued(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = True

        res = client.post('/api/suggestions', json={
            "items": [{"search_query": "Foo - Bar"}]
        })

    data = res.get_json()
    assert data["queued"] == 0
    assert data["skipped"] == 1
    mock_db.queue_download.assert_not_called()


# ---------------------------------------------------------------------------
# /api/catalog
# ---------------------------------------------------------------------------

def test_get_catalog_returns_all_rows_without_since(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.conn.execute.return_value.fetchone.return_value = {"t": "2026-04-17 12:00:00"}
        mock_db.get_catalog_since.return_value = [
            {"mbid": "m1", "title": "Song", "artist": "A", "updated_at": "2026-04-17 11:00:00"},
            {"mbid": "m2", "title": "Song2", "artist": "B", "updated_at": "2026-04-17 11:30:00"},
        ]

        res = client.get('/api/catalog')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] == 2
    assert data["as_of"] == "2026-04-17 12:00:00"
    assert len(data["tracks"]) == 2
    mock_db.get_catalog_since.assert_called_once_with(None)


def test_get_catalog_with_since_filter(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.conn.execute.return_value.fetchone.return_value = {"t": "2026-04-17 13:00:00"}
        mock_db.get_catalog_since.return_value = []

        res = client.get('/api/catalog?since=2026-04-17+12:00:00')

    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 0
    mock_db.get_catalog_since.assert_called_once_with("2026-04-17 12:00:00")


def test_catalog_pull_rejects_when_master_url_missing(client, mock_config):
    mock_config.master_url = ""
    res = client.post('/api/catalog/pull')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "master_url" in data["message"]


def test_catalog_pull_starts_task_when_configured(client, mock_config):
    mock_config.master_url = "http://host.local:5001"
    with patch('web_server.run_catalog_pull') as mock_run:
        res = client.post('/api/catalog/pull')
        # Let the spawned thread see a no-op target
        mock_run.return_value = None

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_playlists_pull_rejects_when_master_url_missing(client, mock_config):
    mock_config.master_url = ""
    res = client.post('/api/playlists/pull')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "master_url" in data["message"]


def test_playlists_pull_starts_task_when_configured(client, mock_config):
    mock_config.master_url = "http://host.local:5001"
    with patch('web_server.run_playlist_pull') as mock_run:
        res = client.post('/api/playlists/pull')
        mock_run.return_value = None

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_post_inventory_writes_snapshot(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.replace_device_inventory.return_value = 2

        res = client.post('/api/inventory', json={
            "device_id": "dev-A",
            "items": [
                {"mbid": "m1", "local_path": "/a"},
                {"mbid": "m2", "local_path": "/b"},
            ],
        })

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["device_id"] == "dev-A"
    assert data["received"] == 2
    assert data["written"] == 2
    mock_db.replace_device_inventory.assert_called_once()


def test_post_inventory_requires_device_id(client, mock_config):
    res = client.post('/api/inventory', json={"items": []})
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False


def test_post_inventory_rejects_non_list_items(client, mock_config):
    res = client.post('/api/inventory', json={
        "device_id": "dev-A",
        "items": "not a list",
    })
    assert res.status_code == 400


def test_get_playlists_delta_returns_rows(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.conn.execute.return_value.fetchone.return_value = {"t": "2026-04-18 12:00:00"}
        mock_db.get_playlists_since.return_value = [
            {
                "playlist_id": "p1",
                "name": "Mix",
                "spotify_url": "",
                "updated_at": "2026-04-18 11:00:00",
                "tracks": [{"track_mbid": "m1", "track_order": 0}],
            },
        ]

        res = client.get('/api/playlists?since=2026-04-18+10:00:00')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["as_of"] == "2026-04-18 12:00:00"
    assert data["count"] == 1
    assert data["playlists"][0]["name"] == "Mix"
    mock_db.get_playlists_since.assert_called_once_with("2026-04-18 10:00:00")


def test_get_playlists_delta_without_since(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.conn.execute.return_value.fetchone.return_value = {"t": "2026-04-18 12:00:00"}
        mock_db.get_playlists_since.return_value = []

        res = client.get('/api/playlists')

    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 0
    mock_db.get_playlists_since.assert_called_once_with(None)


def test_post_suggestions_dedupes_within_payload(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = False

        res = client.post('/api/suggestions', json={
            "items": [
                {"search_query": "Foo - Bar"},
                {"search_query": "foo - bar"},
            ]
        })

    data = res.get_json()
    assert data["received"] == 2
    assert data["queued"] == 1
    assert mock_db.queue_download.call_count == 1
