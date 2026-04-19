import pytest
import json
from unittest.mock import patch, MagicMock
from src.db_manager import DatabaseManager
from web_server import build_suggestion_items, TaskManager

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


def test_inventory_report_rejects_when_disabled(client, mock_config):
    mock_config.report_inventory_to_host = False
    res = client.post('/api/inventory/report')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "report_inventory_to_host" in data["message"]


def test_inventory_report_starts_task_when_enabled(client, mock_config):
    mock_config.report_inventory_to_host = True
    with patch('web_server.run_inventory_report') as mock_run:
        res = client.post('/api/inventory/report')
        mock_run.return_value = None

    assert res.status_code == 200
    assert res.get_json()["success"] is True


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


def test_playlists_push_rejects_when_master_url_missing(client, mock_config):
    mock_config.master_url = ""
    res = client.post('/api/playlists/push')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "master_url" in data["message"]


def test_playlists_push_starts_task_when_configured(client, mock_config):
    mock_config.master_url = "http://host.local:5001"
    with patch('web_server.run_playlist_push') as mock_run:
        res = client.post('/api/playlists/push')
        mock_run.return_value = None

    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_post_playlists_accepts_and_reports_per_row(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.apply_pushed_playlist_row.side_effect = ["inserted", "stale", "skipped"]

        res = client.post('/api/playlists', json={
            "playlists": [
                {"playlist_id": "p1", "updated_at": "2026-04-18 12:00:00"},
                {"playlist_id": "p2", "updated_at": "2026-04-18 09:00:00"},
                {},
            ],
        })

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["received"] == 3
    assert data["accepted"] == 1
    assert data["stale"] == 1
    assert data["skipped"] == 1
    assert [r["result"] for r in data["results"]] == ["inserted", "stale", "skipped"]


def test_post_playlists_rejects_non_list(client, mock_config):
    res = client.post('/api/playlists', json={"playlists": "not-a-list"})
    assert res.status_code == 400


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


def test_fleet_summary_returns_devices(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_fleet_summary.return_value = [
            {"device_id": "dev-A", "track_count": 3, "last_reported_at": "2026-04-18"},
        ]
        res = client.get('/api/fleet/summary')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["devices"][0]["device_id"] == "dev-A"


def test_fleet_track_lookup_by_mbid(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_devices_holding_mbid.return_value = [
            {"device_id": "dev-A", "local_path": "/a", "reported_at": "2026-04-18"},
        ]
        res = client.get('/api/fleet/track?mbid=m1')
    data = res.get_json()
    assert data["success"] is True
    assert data["mbid"] == "m1"
    assert data["holders"][0]["device_id"] == "dev-A"


def test_fleet_track_lookup_by_query_enriches_with_holders(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.find_tracks_for_fleet_search.return_value = [
            {"mbid": "m1", "artist": "A", "title": "T", "album": "X", "device_count": 1},
        ]
        mock_db.get_devices_holding_mbid.return_value = [
            {"device_id": "dev-A", "local_path": "/a", "reported_at": "2026-04-18"},
        ]
        res = client.get('/api/fleet/track?q=title')
    data = res.get_json()
    assert data["success"] is True
    assert data["results"][0]["holders"][0]["device_id"] == "dev-A"


def test_fleet_track_lookup_requires_param(client, mock_config):
    res = client.get('/api/fleet/track')
    assert res.status_code == 400


def test_fleet_page_renders(client, mock_config):
    res = client.get('/fleet')
    assert res.status_code == 200
    assert b"Fleet View" in res.data


def _config_roundtrip_fixtures(tmp_path, monkeypatch):
    """Point web_server.CONFIG_FILE at a temp file and return its path."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "database_file": "dap.db",
        "music_library_path": "/music",
        "slsk_password": "supersecret",
        "jellyfin_api_key": "key-abc",
        "master_url": "",
        "is_master": True,
        "report_inventory_to_host": False,
    }))
    import web_server as ws
    monkeypatch.setattr(ws, "CONFIG_FILE", str(cfg_path))
    return cfg_path


def test_get_config_redacts_secrets(client, mock_config, tmp_path, monkeypatch):
    _config_roundtrip_fixtures(tmp_path, monkeypatch)
    res = client.get('/api/config')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["config"]["slsk_password"] == ""
    assert data["config"]["jellyfin_api_key"] == ""
    assert data["config"]["music_library_path"] == "/music"
    assert "slsk_password" in data["secret_keys"]


def test_post_config_merges_and_ignores_unknown_keys(client, mock_config, tmp_path, monkeypatch):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    res = client.post('/api/config', json={
        "music_library_path": "/new/music",
        "master_url": "http://host:5001",
        "report_inventory_to_host": True,
        "database_file": "HACKED.db",  # not in editable set — must be ignored
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert set(data["changed"]) == {"music_library_path", "master_url", "report_inventory_to_host"}

    saved = json.loads(cfg_path.read_text())
    assert saved["music_library_path"] == "/new/music"
    assert saved["master_url"] == "http://host:5001"
    assert saved["report_inventory_to_host"] is True
    assert saved["database_file"] == "dap.db"  # untouched


def test_post_config_blank_secret_keeps_existing(client, mock_config, tmp_path, monkeypatch):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    res = client.post('/api/config', json={
        "slsk_password": "",  # blank → keep current
        "jellyfin_api_key": "new-key",  # non-blank → update
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["changed"] == ["jellyfin_api_key"]

    saved = json.loads(cfg_path.read_text())
    assert saved["slsk_password"] == "supersecret"
    assert saved["jellyfin_api_key"] == "new-key"


def test_post_config_rejects_non_object(client, mock_config, tmp_path, monkeypatch):
    _config_roundtrip_fixtures(tmp_path, monkeypatch)
    res = client.post('/api/config', json=[1, 2, 3])
    assert res.status_code == 400


def test_soft_delete_track_route_stamps(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.soft_delete_track.return_value = True
        res = client.delete('/api/tracks/abc123')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["deleted"] is True
    assert data["mbid"] == "abc123"
    mock_db.soft_delete_track.assert_called_once_with("abc123")


def test_soft_delete_track_route_reports_no_op(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.soft_delete_track.return_value = False
        res = client.delete('/api/tracks/missing')
    data = res.get_json()
    assert data["success"] is True
    assert data["deleted"] is False


def test_sync_all_starts_task(client, mock_config):
    res = client.post('/api/sync/all')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_sync_state_returns_four_cursors(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_sync_state.side_effect = lambda key: {
            "last_catalog_sync": "2026-04-19 10:00:00",
            "last_playlist_sync": "2026-04-19 10:01:00",
            "last_playlist_push": None,
            "last_inventory_report": "2026-04-19 10:02:00",
        }[key]
        res = client.get('/api/sync/state')
    data = res.get_json()
    assert data["success"] is True
    assert data["state"]["catalog_pull"] == "2026-04-19 10:00:00"
    assert data["state"]["playlist_pull"] == "2026-04-19 10:01:00"
    assert data["state"]["playlist_push"] is None
    assert data["state"]["inventory_report"] == "2026-04-19 10:02:00"


def test_soft_delete_playlist_route(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.soft_delete_playlist.return_value = True
        res = client.delete('/api/playlists/p1')
    data = res.get_json()
    assert data["success"] is True
    assert data["deleted"] is True
    mock_db.soft_delete_playlist.assert_called_once_with("p1")


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


# ---------------------------------------------------------------------------
# /api/* bearer-token auth (opt-in via api_token in config)
# ---------------------------------------------------------------------------

@pytest.fixture
def _token_config(monkeypatch):
    """Install a config object with a real _config dict carrying api_token."""
    import web_server
    mock = MagicMock()
    mock.db_path = ":memory:"
    mock._config = {"api_token": "secret-xyz"}
    monkeypatch.setattr(web_server, "config", mock)
    monkeypatch.setattr(web_server, "task_manager", TaskManager())
    return mock


def test_api_requires_token_when_configured(client, _token_config):
    res = client.get('/api/stats')
    assert res.status_code == 401
    data = res.get_json()
    assert data["success"] is False
    assert "bearer" in data["message"].lower()


def test_api_rejects_wrong_token(client, _token_config):
    res = client.get('/api/stats', headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401
    assert "invalid" in res.get_json()["message"].lower()


def test_api_accepts_correct_token(client, _token_config):
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('shutil.disk_usage') as mock_du:
        mock_instance = MockDB.return_value.__enter__.return_value
        mock_instance.get_library_stats.return_value = {
            'tracks': 1, 'artists': 1, 'albums': 1, 'playlists': 0, 'incomplete_albums': 0
        }
        mock_du.return_value = (100*1024**3, 50*1024**3, 50*1024**3)
        res = client.get('/api/stats', headers={"Authorization": "Bearer secret-xyz"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_api_status_is_exempt_from_token(client, _token_config):
    """Health checks must not require the token."""
    res = client.get('/api/status')
    assert res.status_code == 200


def test_api_open_mode_when_token_empty(client, mock_config):
    """Existing behavior: no token set => no auth enforced."""
    res = client.get('/api/status')
    assert res.status_code == 200


def test_api_token_blocks_post_routes_too(client, _token_config):
    res = client.post('/api/suggestions', json={"items": []})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# /api/tag/identify + /api/tag/apply (Picard-style tagging)
# ---------------------------------------------------------------------------

@pytest.fixture
def _tag_config(monkeypatch):
    """Config with AcoustID key set, so the tag identify route can proceed."""
    import web_server
    mock = MagicMock()
    mock.db_path = ":memory:"
    mock._config = {"acoustid_api_key": "key-123", "contact_email": "t@t"}
    monkeypatch.setattr(web_server, "config", mock)
    monkeypatch.setattr(web_server, "task_manager", TaskManager())
    return mock


def test_tag_identify_requires_acoustid_key(client, mock_config):
    mock_config._config = {}
    res = client.post('/api/tag/identify/m1')
    assert res.status_code == 400
    assert "acoustid_api_key" in res.get_json()["message"]


def test_tag_identify_404_when_track_has_no_local_path(client, _tag_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = None
        mock_db.get_track_by_mbid.return_value = mock_track
        res = client.post('/api/tag/identify/m1')
    assert res.status_code == 404


def test_tag_identify_returns_candidate(client, _tag_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = "/music/song.flac"
        mock_db.get_track_by_mbid.return_value = mock_track

        candidate = {
            "score": 0.95,
            "tier": "green",
            "meta": {"artist": "A", "title": "T", "album": "Alb", "mbid": "rec1"},
            "current": {"title": "old"},
        }
        with patch('src.tag_service.identify_file', return_value=candidate):
            res = client.post('/api/tag/identify/m1')

    data = res.get_json()
    assert data["success"] is True
    assert data["candidate"]["tier"] == "green"
    assert data["candidate"]["meta"]["mbid"] == "rec1"


def test_tag_identify_reports_no_match_gracefully(client, _tag_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = "/music/song.flac"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.identify_file', return_value=None):
            with patch('src.tag_service.read_current_tags', return_value={"title": "old"}):
                res = client.post('/api/tag/identify/m1')

    data = res.get_json()
    assert data["success"] is True
    assert data["candidate"] is None


def test_tag_apply_writes_tags_and_updates_db(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.mbid = "m1"
        mock_track.local_path = "/music/song.flac"
        mock_track.title = "old title"
        mock_track.artist = "old artist"
        mock_track.album = "old album"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.write_tags', return_value="flac") as mock_write:
            res = client.post('/api/tag/apply/m1', json={
                "meta": {
                    "title": "New", "artist": "Na",
                    "album": "Nb", "mbid": "m1"
                }
            })

    data = res.get_json()
    assert data["success"] is True
    assert data["container"] == "flac"
    mock_write.assert_called_once()
    mock_db.add_or_update_track.assert_called_once()
    mock_db.soft_delete_track.assert_not_called()  # same mbid


def test_tag_apply_soft_deletes_old_row_when_mbid_changes(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.mbid = "old-m"
        mock_track.local_path = "/music/song.flac"
        mock_track.title = "t"
        mock_track.artist = "a"
        mock_track.album = "b"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.write_tags', return_value="flac"):
            res = client.post('/api/tag/apply/old-m', json={
                "meta": {"title": "T", "artist": "A", "mbid": "new-m"}
            })

    data = res.get_json()
    assert data["success"] is True
    assert data["mbid"] == "new-m"
    assert data["previous_mbid"] == "old-m"
    mock_db.soft_delete_track.assert_called_once_with("old-m")


def test_tag_apply_rejects_missing_title(client, mock_config):
    res = client.post('/api/tag/apply/m1', json={"meta": {"artist": "A"}})
    assert res.status_code == 400


def test_tag_apply_404_when_no_local_path(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = None
        mock_db.get_track_by_mbid.return_value = mock_track
        res = client.post('/api/tag/apply/m1', json={"meta": {"title": "T"}})
    assert res.status_code == 404
