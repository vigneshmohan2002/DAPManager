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


def test_healthz_ok_when_config_loaded(client, mock_config):
    res = client.get('/api/healthz')
    assert res.status_code == 200
    data = res.get_json()
    assert data == {"ok": True, "initialized": True}


def test_healthz_unauthenticated_with_token_set(client, _token_config):
    # /api/healthz is exempt from Bearer-token gate so container probes work.
    res = client.get('/api/healthz')
    assert res.status_code == 200

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


def test_catalog_link_local_rejects_without_library_configured(client, mock_config):
    # A MagicMock returns truthy for any attribute by default — tests
    # elsewhere rely on that. Force music_library to empty to exercise
    # the rejection branch.
    mock_config.music_library = ""
    res = client.post('/api/catalog/link-local')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "music_library_path" in data["message"]


def test_catalog_link_local_starts_task_when_configured(client, mock_config):
    mock_config.music_library = "/music"
    with patch('web_server.run_catalog_link_local') as mock_run:
        mock_run.return_value = None
        res = client.post('/api/catalog/link-local')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True


def test_catalog_queue_download_rejects_bad_body(client, mock_config):
    res = client.post('/api/catalog/queue-download', json={"mbids": "not-a-list"})
    assert res.status_code == 400


def test_catalog_queue_download_buckets_each_mbid(client, mock_config):
    from src.db_manager import Track
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        # m-queue: live, unlinked → should be enqueued.
        # m-linked: has local_path → skipped_linked.
        # m-dup: unlinked but query already queued → skipped_queued.
        # m-missing: not in catalog → not_found.
        # "": blank in the input → not_found.
        tracks = {
            "m-queue": Track(mbid="m-queue", title="T1", artist="A1"),
            "m-linked": Track(mbid="m-linked", title="T2", artist="A2", local_path="/a"),
            "m-dup": Track(mbid="m-dup", title="T3", artist="A3"),
        }
        mock_db.get_track_by_mbid.side_effect = lambda m: tracks.get(m)
        mock_db.is_download_queued.side_effect = lambda q: (q == "A3 - T3")

        res = client.post('/api/catalog/queue-download', json={
            "mbids": ["m-queue", "m-linked", "m-dup", "m-missing", ""],
        })

    assert res.status_code == 200
    data = res.get_json()
    assert data["received"] == 5
    assert data["queued"] == 1
    assert data["queued_mbids"] == ["m-queue"]
    assert data["skipped_linked"] == 1
    assert data["skipped_queued"] == 1
    assert data["not_found"] == 2

    # queue_download called exactly once, with the expected shape.
    assert mock_db.queue_download.call_count == 1
    item = mock_db.queue_download.call_args.args[0]
    assert item.search_query == "A1 - T1"
    assert item.playlist_id == "CATALOG"
    assert item.mbid_guess == "m-queue"
    assert item.status == "pending"


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


def test_library_albums_lists_albums(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        instance = MockDB.return_value.__enter__.return_value
        instance.list_albums.return_value = [
            {"id": "rmb-1", "title": "Album One", "artist": "A", "track_count": 10, "cover_path": "/m/1.flac"},
            {"id": "Y|X", "title": "Y", "artist": "X", "track_count": 5, "cover_path": "/m/2.flac"},
        ]
        res = client.get('/api/library/albums')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert len(data["albums"]) == 2
    # cover_path must not leak to the webview.
    assert "cover_path" not in data["albums"][0]
    assert data["albums"][0]["id"] == "rmb-1"
    assert data["albums"][0]["track_count"] == 10


def test_library_tracks_tags_availability_and_filters_unavailable(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_all_tracks.return_value = [
            {"mbid": "t-local", "title": "L", "artist": "A", "album": "Al",
             "track_number": 1, "disc_number": 1, "album_id": "rmb",
             "local_path": "/m/l.flac", "dap_path": None},
            {"mbid": "t-drive", "title": "D", "artist": "A", "album": "Al",
             "track_number": 2, "disc_number": 1, "album_id": "rmb",
             "local_path": None, "dap_path": "/dap/d.flac"},
            {"mbid": "t-nowhere", "title": "N", "artist": "A", "album": "Al",
             "track_number": 3, "disc_number": 1, "album_id": "rmb",
             "local_path": None, "dap_path": None},
        ]
        res = client.get('/api/library/tracks')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    # No master configured → catalog-only row drops out entirely; the
    # two rows that resolve on-disk are tagged with their tier.
    tiers = {t["mbid"]: t["availability"] for t in data["tracks"]}
    assert tiers == {"t-local": "local", "t-drive": "drive"}
    # Path columns must never leak to the webview.
    assert all("local_path" not in t and "dap_path" not in t for t in data["tracks"])


def test_library_tracks_tags_remote_when_master_configured(client, mock_config):
    # Availability is a pre-play decision and doesn't probe the master
    # at listing time — so a configured master_url flips catalog-only
    # rows from "unavailable" to "remote" even if the master is down.
    mock_config._config = {"master_url": "http://master.example"}
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_all_tracks.return_value = [
            {"mbid": "t-nowhere", "title": "N", "artist": "A", "album": "Al",
             "track_number": 3, "disc_number": 1, "album_id": "rmb",
             "local_path": None, "dap_path": None},
        ]
        res = client.get('/api/library/tracks')

    data = res.get_json()
    assert data["tracks"][0]["availability"] == "remote"


def test_library_tracks_forwards_filter_params_to_db(client, mock_config):
    # With any of playlist_id / local_only / include_orphans set, the
    # route hits list_tracks_filtered (not list_all_tracks) and passes
    # the params through.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.list_tracks_filtered.return_value = [
            {"mbid": "t-local", "title": "L", "artist": "A", "album": "Al",
             "track_number": 1, "disc_number": 1, "album_id": "rmb",
             "local_path": "/m/l.flac", "dap_path": None, "deleted_at": None},
        ]
        res = client.get('/api/library/tracks?playlist_id=pl-1&local_only=1&include_orphans=1')

    assert res.status_code == 200
    assert res.get_json()["tracks"][0]["mbid"] == "t-local"
    inst.list_tracks_filtered.assert_called_once_with(
        playlist_id="pl-1", local_only=True, include_orphans=True,
    )
    # list_all_tracks must NOT have been called when filters are in play.
    assert not inst.list_all_tracks.called


def test_library_tracks_include_orphans_keeps_unavailable_rows(client, mock_config):
    # An orphan row with no playable source stays visible when the UI
    # asked for orphans — otherwise the /library 'Show orphans' toggle
    # would silently drop the rows a user is trying to restore.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.list_tracks_filtered.return_value = [
            {"mbid": "t-dead", "title": "X", "artist": "A", "album": "Al",
             "track_number": 1, "disc_number": 1, "album_id": "rmb",
             "local_path": None, "dap_path": None,
             "deleted_at": "2026-04-20 10:00:00"},
        ]
        res = client.get('/api/library/tracks?include_orphans=1')

    data = res.get_json()
    assert len(data["tracks"]) == 1
    assert data["tracks"][0]["orphan"] is True
    assert data["tracks"][0]["availability"] == "unavailable"


def test_library_playlists_lists_live_playlists(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_playlists_with_counts.return_value = [
            {"playlist_id": "pl-1", "name": "Alpha", "track_count": 12, "updated_at": "2026-04-20 10:00:00"},
            {"playlist_id": "pl-2", "name": "Bravo", "track_count": 0, "updated_at": "2026-04-20 11:00:00"},
        ]
        res = client.get('/api/library/playlists')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert [p["name"] for p in data["playlists"]] == ["Alpha", "Bravo"]
    assert data["playlists"][0]["track_count"] == 12


def test_library_playlists_create_returns_generated_id(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.create_playlist.return_value = "new-uuid-hex"
        res = client.post('/api/library/playlists', json={"name": "  Fresh  "})

    assert res.status_code == 201
    data = res.get_json()
    assert data == {"success": True, "playlist_id": "new-uuid-hex", "name": "Fresh"}


def test_library_playlists_create_rejects_empty_name(client, mock_config):
    res = client.post('/api/library/playlists', json={"name": "  "})
    assert res.status_code == 400
    assert res.get_json()["success"] is False


def test_library_playlist_update_rename_only(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_playlist.return_value = MagicMock(name="existing")
        inst.rename_playlist.return_value = True
        res = client.put('/api/library/playlists/pl-1', json={"name": "Renamed"})

    assert res.status_code == 200
    data = res.get_json()
    assert data == {"success": True, "playlist_id": "pl-1", "renamed": True}
    inst.rename_playlist.assert_called_once_with("pl-1", "Renamed")
    # membership helper must NOT have been touched when track_mbids is absent.
    assert not inst.replace_playlist_membership.called


def test_library_playlist_update_replaces_membership(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_playlist.return_value = MagicMock()
        inst.replace_playlist_membership.return_value = 2
        res = client.put(
            '/api/library/playlists/pl-1',
            json={"track_mbids": ["a", "b", "ghost"]},
        )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["landed"] == 2
    assert data["requested"] == 3
    assert data["renamed"] is False
    inst.replace_playlist_membership.assert_called_once_with("pl-1", ["a", "b", "ghost"])


def test_library_playlist_update_empty_list_empties_playlist(client, mock_config):
    # Explicit empty list means "empty the playlist" — distinct from
    # omitting the key entirely (which leaves membership alone).
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_playlist.return_value = MagicMock()
        inst.replace_playlist_membership.return_value = 0
        res = client.put('/api/library/playlists/pl-1', json={"track_mbids": []})

    assert res.status_code == 200
    inst.replace_playlist_membership.assert_called_once_with("pl-1", [])


def test_library_playlist_update_requires_name_or_tracks(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_playlist.return_value = MagicMock()
        res = client.put('/api/library/playlists/pl-1', json={})
    assert res.status_code == 400


def test_library_playlist_update_404s_missing(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_playlist.return_value = None
        res = client.put('/api/library/playlists/pl-nope', json={"name": "X"})
    assert res.status_code == 404


def test_library_playlist_delete_delegates_to_soft_delete(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.soft_delete_playlist.return_value = True
        res = client.delete('/api/library/playlists/pl-1')

    assert res.status_code == 200
    data = res.get_json()
    assert data == {"success": True, "deleted": True, "playlist_id": "pl-1"}


def test_library_playlist_delete_forwards_purge_flag(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.purge_playlist.return_value = True
        res = client.delete('/api/library/playlists/pl-1?purge=true')

    assert res.status_code == 200
    data = res.get_json()
    assert data["purged"] is True


def test_library_artists_lists_artists(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_artists.return_value = [
            {"name": "Alpha", "album_count": 2, "track_count": 20},
            {"name": "Bravo", "album_count": 1, "track_count": 8},
        ]
        res = client.get('/api/library/artists')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert [a["name"] for a in data["artists"]] == ["Alpha", "Bravo"]
    assert data["artists"][0]["album_count"] == 2


def test_library_album_cover_returns_bytes(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.cover_art.extract_cover', return_value=(b"JPEG", "image/jpeg")):
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = "/m/1.flac"
        res = client.get('/api/library/albums/rmb-1/cover')

    assert res.status_code == 200
    assert res.mimetype == "image/jpeg"
    assert res.data == b"JPEG"
    assert "max-age" in res.headers.get("Cache-Control", "")


def test_library_album_cover_404_when_no_path(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None
        res = client.get('/api/library/albums/nope/cover')
    assert res.status_code == 404


def test_library_album_cover_404_when_no_embedded_art(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.cover_art.extract_cover', return_value=None):
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = "/m/1.flac"
        res = client.get('/api/library/albums/rmb-1/cover')
    assert res.status_code == 404


def test_library_album_tracks_returns_ordered_list(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_album_tracks.return_value = [
            {"mbid": "t-a", "title": "A", "artist": "X", "album": "Y",
             "track_number": 1, "disc_number": 1, "local_path": "/m/a.flac"},
            {"mbid": "t-b", "title": "B", "artist": "X", "album": "Y",
             "track_number": 2, "disc_number": 1, "local_path": "/m/b.flac"},
        ]
        res = client.get('/api/library/albums/rmb-1/tracks')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert [t["mbid"] for t in data["tracks"]] == ["t-a", "t-b"]
    # Local path must not leak to the webview.
    assert "local_path" not in data["tracks"][0]


def test_stream_serves_file_with_audio_mime(client, mock_config, tmp_path):
    f = tmp_path / "track.flac"
    f.write_bytes(b"FLACBYTES")
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": str(f), "dap_path": None,
        }
        res = client.get('/api/stream/t-a')

    assert res.status_code == 200
    assert res.mimetype == "audio/flac"
    assert res.data == b"FLACBYTES"


def test_stream_supports_range_request(client, mock_config, tmp_path):
    f = tmp_path / "track.mp3"
    f.write_bytes(b"0123456789")
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": str(f), "dap_path": None,
        }
        res = client.get('/api/stream/t-a', headers={"Range": "bytes=2-5"})

    assert res.status_code == 206
    assert res.data == b"2345"
    assert res.headers.get("Content-Range", "").startswith("bytes 2-5/")


def test_stream_falls_back_to_dap_path(client, mock_config, tmp_path):
    # local_path missing on disk → resolver should drop through to the
    # drive path rather than bailing out or hitting the master proxy.
    f = tmp_path / "drive.flac"
    f.write_bytes(b"DAPBYTES")
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": "/nonexistent/local.flac", "dap_path": str(f),
        }
        res = client.get('/api/stream/t-a')

    assert res.status_code == 200
    assert res.data == b"DAPBYTES"


def test_stream_404_when_track_missing(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = None
        res = client.get('/api/stream/nope')
    assert res.status_code == 404


def test_stream_404_when_no_source_resolves(client, mock_config):
    # Row exists but neither path points at a real file and no master
    # is configured → 404 rather than a 500.
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": "/nonexistent/file.flac", "dap_path": None,
        }
        res = client.get('/api/stream/t-a')
    assert res.status_code == 404


def test_stream_proxies_master_when_no_local_source(client, mock_config):
    # No on-disk source → proxy to the master's /api/stream. The helper
    # forwards the Range header and relays status + body verbatim. We
    # leave api_token empty here so the satellite's own auth gate stays
    # open; a separate test covers bearer forwarding.
    mock_config._config = {"master_url": "http://master.example/"}
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": None, "dap_path": None,
        }
        upstream = MagicMock()
        upstream.status_code = 206
        upstream.headers = {
            "Content-Type": "audio/flac",
            "Content-Range": "bytes 0-3/10",
            "Content-Length": "4",
            "Accept-Ranges": "bytes",
        }
        upstream.iter_content.return_value = iter([b"ABCD"])
        mock_get.return_value = upstream

        res = client.get('/api/stream/t-a', headers={"Range": "bytes=0-3"})

    assert res.status_code == 206
    assert res.data == b"ABCD"
    assert res.headers.get("Content-Range") == "bytes 0-3/10"
    call = mock_get.call_args
    assert call.args[0] == "http://master.example/api/stream/t-a"
    assert call.kwargs["headers"]["Range"] == "bytes=0-3"
    assert "Authorization" not in call.kwargs["headers"]


def test_stream_proxy_forwards_bearer_token(client, mock_config):
    # When api_token is configured, the proxy attaches it upstream so
    # the master's own auth gate accepts the request. We auth the local
    # client-side hit too so the satellite's gate passes first.
    mock_config._config = {
        "master_url": "http://master.example",
        "api_token": "tok",
    }
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": None, "dap_path": None,
        }
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "audio/flac"}
        upstream.iter_content.return_value = iter([b"X"])
        mock_get.return_value = upstream

        res = client.get('/api/stream/t-a', headers={"Authorization": "Bearer tok"})

    assert res.status_code == 200
    call = mock_get.call_args
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_stream_proxy_returns_502_on_upstream_failure(client, mock_config):
    # Master unreachable → we surface a clean 502 instead of a 500 so
    # the UI can distinguish "track missing" (404) from "network".
    mock_config._config = {"master_url": "http://master.example"}
    import requests as _r
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get', side_effect=_r.ConnectionError("boom")):
        MockDB.return_value.__enter__.return_value.get_track_sources.return_value = {
            "local_path": None, "dap_path": None,
        }
        res = client.get('/api/stream/t-a')

    assert res.status_code == 502


def test_fleet_track_lookup_requires_param(client, mock_config):
    res = client.get('/api/fleet/track')
    assert res.status_code == 400


def test_fleet_page_renders(client, mock_config):
    res = client.get('/fleet')
    assert res.status_code == 200
    assert b"Fleet View" in res.data


def test_orphans_page_renders(client, mock_config):
    res = client.get('/orphans')
    assert res.status_code == 200
    assert b"Orphans" in res.data
    # Both tabs present in the initial shell.
    assert b'data-tab="tracks"' in res.data
    assert b'data-tab="playlists"' in res.data


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


def test_delete_track_route_purges_when_flagged(client, mock_config):
    # ?purge=true routes to purge_track and returns {"purged": ...}
    # instead of {"deleted": ...} so the UI can tell which happened.
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.purge_track.return_value = True
        res = client.delete('/api/tracks/abc123?purge=true')
    assert res.status_code == 200
    data = res.get_json()
    assert data["purged"] is True
    assert "deleted" not in data
    mock_db.purge_track.assert_called_once_with("abc123")
    mock_db.soft_delete_track.assert_not_called()


def test_delete_playlist_route_purges_when_flagged(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.purge_playlist.return_value = True
        res = client.delete('/api/playlists/p1?purge=1')
    data = res.get_json()
    assert data["purged"] is True
    mock_db.purge_playlist.assert_called_once_with("p1")


def test_restore_track_route(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.restore_track.return_value = True
        res = client.post('/api/tracks/abc/restore')
    assert res.status_code == 200
    data = res.get_json()
    assert data["restored"] is True
    assert data["mbid"] == "abc"


def test_restore_playlist_route(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.restore_playlist.return_value = True
        res = client.post('/api/playlists/p1/restore')
    data = res.get_json()
    assert data["restored"] is True


def test_orphan_tracks_lists_soft_deleted(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_orphan_tracks.return_value = [
            {"mbid": "t1", "artist": "A", "title": "T", "album": "Al",
             "deleted_at": "2026-04-21 10:00:00", "local_path": "/a.flac"},
        ]
        res = client.get('/api/orphans/tracks')
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["tracks"][0]["mbid"] == "t1"
    assert data["tracks"][0]["local_path"] == "/a.flac"


def test_orphan_playlists_lists_soft_deleted(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_orphan_playlists.return_value = [
            {"playlist_id": "p1", "name": "Mix",
             "deleted_at": "2026-04-21 10:00:00", "track_count": 3},
        ]
        res = client.get('/api/orphans/playlists')
    data = res.get_json()
    assert data["playlists"][0]["track_count"] == 3


def test_delete_track_file_refuses_on_live_row(client, mock_config):
    # A 409 here means the user tried to delete the file of a track
    # that isn't soft-deleted yet — the UI should soft-delete first.
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_orphan_tracks.return_value = []  # row not an orphan
        res = client.delete('/api/tracks/live-mbid/file')
    assert res.status_code == 409
    mock_db.update_track_local_path.assert_not_called()


def test_delete_track_file_removes_and_clears_path(client, mock_config, tmp_path):
    f = tmp_path / "to-delete.flac"
    f.write_bytes(b"X")
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_orphan_tracks.return_value = [
            {"mbid": "t1", "artist": "A", "title": "T", "album": "Al",
             "deleted_at": "2026-04-21", "local_path": str(f)},
        ]
        res = client.delete('/api/tracks/t1/file')
    assert res.status_code == 200
    data = res.get_json()
    assert data["removed"] is True
    assert not f.exists()
    mock_db.update_track_local_path.assert_called_once_with("t1", None)


def test_delete_track_file_idempotent_when_file_already_gone(client, mock_config):
    # File pointed at by local_path was manually deleted — the API
    # still clears the column cleanly so the orphan doesn't keep
    # showing a phantom "on disk" badge.
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_orphan_tracks.return_value = [
            {"mbid": "t1", "artist": "A", "title": "T", "album": "Al",
             "deleted_at": "2026-04-21", "local_path": "/vanished/x.flac"},
        ]
        res = client.delete('/api/tracks/t1/file')
    assert res.status_code == 200
    data = res.get_json()
    assert data["removed"] is False
    mock_db.update_track_local_path.assert_called_once_with("t1", None)


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
                },
                "score": 0.72,
            })

    data = res.get_json()
    assert data["success"] is True
    assert data["container"] == "flac"
    mock_write.assert_called_once()
    mock_db.add_or_update_track.assert_called_once()
    mock_db.soft_delete_track.assert_not_called()  # same mbid
    # Apply = user confirmation: clear the flag by stamping green.
    mock_db.set_track_tag_tier.assert_called_once_with("m1", "green", 0.72)


def test_tag_apply_stamps_green_without_score(client, mock_config):
    """Score is optional in the apply body — tier should still go green."""
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.mbid = "m1"
        mock_track.local_path = "/music/song.flac"
        mock_track.title = "t"; mock_track.artist = "a"; mock_track.album = "b"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.write_tags', return_value="flac"):
            res = client.post('/api/tag/apply/m1', json={
                "meta": {"title": "T", "artist": "A", "mbid": "m1"}
            })

    assert res.get_json()["success"] is True
    mock_db.set_track_tag_tier.assert_called_once_with("m1", "green", None)


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


# ---------------------------------------------------------------------------
# /api/tracks/needs-review
# ---------------------------------------------------------------------------

def test_tracks_needs_review_returns_flagged_only(client, mock_config):
    from src.db_manager import Track
    flagged = [
        Track(mbid="y", title="Y", artist="Ya", album="Ab",
              local_path="/y.flac", tag_tier="yellow", tag_score=0.72),
        Track(mbid="r", title="R", artist="Ra", album="Rb",
              local_path="/r.flac", tag_tier="red", tag_score=0.3),
    ]
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_tracks_needing_tag_review.return_value = flagged
        res = client.get('/api/tracks/needs-review')

    data = res.get_json()
    assert res.status_code == 200
    assert data["success"] is True
    assert data["count"] == 2
    mbids = {t["mbid"] for t in data["tracks"]}
    assert mbids == {"y", "r"}
    assert data["tracks"][0]["tag_tier"] == "yellow"
    assert data["tracks"][0]["tag_score"] == 0.72


def test_tracks_needs_review_empty_backlog(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_tracks_needing_tag_review.return_value = []
        res = client.get('/api/tracks/needs-review')
    data = res.get_json()
    assert data == {"success": True, "count": 0, "tracks": []}


def test_tracks_needs_review_401_when_token_set_and_missing(client, _token_config):
    # /api/status is the only exempt path — this one should be gated.
    res = client.get('/api/tracks/needs-review')
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# /api/download/request — satellite → master download handoff
# ---------------------------------------------------------------------------

def test_request_download_queues_new_item(client, mock_config):
    mock_config.is_master = True
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = False
        mock_db.queue_download.return_value = 42

        res = client.post('/api/download/request', json={
            "search_query": "Artist - Track",
            "mbid_guess": "mb-123",
            "playlist_id": "SAT-xyz",
        })

    data = res.get_json()
    assert res.status_code == 200
    assert data == {"success": True, "queued": True, "item_id": 42, "message": "queued"}
    # DownloadItem passed through untouched
    (item,), _ = mock_db.queue_download.call_args
    assert item.search_query == "Artist - Track"
    assert item.mbid_guess == "mb-123"
    assert item.playlist_id == "SAT-xyz"


def test_request_download_dedupes_on_repeat(client, mock_config):
    mock_config.is_master = True
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = True

        res = client.post('/api/download/request', json={"search_query": "Dupe"})

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["queued"] is False
    assert "item_id" not in data
    mock_db.queue_download.assert_not_called()


def test_request_download_defaults_playlist_id(client, mock_config):
    mock_config.is_master = True
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.is_download_queued.return_value = False
        mock_db.queue_download.return_value = 1

        client.post('/api/download/request', json={"search_query": "Just a query"})

    (item,), _ = mock_db.queue_download.call_args
    assert item.playlist_id == "SATELLITE"
    assert item.mbid_guess == ""


def test_request_download_rejects_non_master(client, mock_config):
    mock_config.is_master = False
    res = client.post('/api/download/request', json={"search_query": "x"})
    assert res.status_code == 400
    assert res.get_json()["success"] is False


def test_request_download_rejects_empty_query(client, mock_config):
    mock_config.is_master = True
    res = client.post('/api/download/request', json={"search_query": "   "})
    assert res.status_code == 400
    assert "search_query" in res.get_json()["message"]


def test_request_download_rejects_missing_body(client, mock_config):
    mock_config.is_master = True
    res = client.post('/api/download/request', json={})
    assert res.status_code == 400
