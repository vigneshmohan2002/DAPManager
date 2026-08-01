import pytest
import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from src.db_manager import DatabaseManager
import web_server
from web_server import build_suggestion_items, TaskManager


@pytest.mark.parametrize(
    ("page_path", "script_path", "requires_config"),
    [
        ("/setup", "/static/js/setup.js", False),
        ("/library", "/static/js/library.js", True),
        ("/player", "/static/js/player.js", True),
        ("/satellite", "/static/js/satellite.js", True),
        ("/", "/static/js/dashboard.js", True),
        ("/orphans", "/static/js/orphans.js", True),
        ("/contributions", "/static/js/contributions.js", True),
        ("/fleet", "/static/js/fleet.js", True),
    ],
)
def test_browser_pages_load_external_controllers(
    client,
    mock_config,
    monkeypatch,
    tmp_path,
    page_path,
    script_path,
    requires_config,
):
    """Large page controllers stay cacheable and out of rendered markup."""
    config_path = tmp_path / "config.json"
    if requires_config:
        config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(config_path))

    response = client.get(page_path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'<script src="{script_path}?v=4"></script>' in html
    inline_scripts = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>",
        html,
        flags=re.DOTALL,
    )
    assert all(len(body.strip()) < 500 for body in inline_scripts)

    controller = client.get(script_path)
    assert controller.status_code == 200
    assert controller.get_data(as_text=True).startswith("// @ts-check\n")


def test_satellite_album_progress_storage_is_scoped_to_configured_master(
    client,
    mock_config,
):
    script = client.get("/static/js/satellite.js").get_data(as_text=True)

    assert 'api("GET", "/api/config")' in script
    assert 'parsed.protocol !== "http:" && parsed.protocol !== "https:"' in script
    assert "parsed.username || parsed.password || parsed.search || parsed.hash" in script
    assert "return `master:${parsed.origin}${pathname}`" in script
    assert 'return `master:${rawMasterUrl' not in script
    assert "`${ALBUM_REQUEST_STORAGE_PREFIX}:${suffix}`" in script
    assert "`${ALBUM_REQUEST_DISMISSED_STORAGE_PREFIX}:${suffix}`" in script
    assert "Without a verified authority identity" in script


def test_satellite_album_storage_scope_rejects_unsafe_authority_urls():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the satellite controller")
    source = (
        Path(web_server.app.static_folder) / "js" / "satellite.js"
    ).read_text(encoding="utf-8")
    marker = "function albumRequestMasterScope(config)"
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    end = None
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    function_source = source[start:end]
    configs = [
        {
            "device_role": "satellite",
            "master_url": "HTTPS://MASTER.EXAMPLE:443/base/",
        },
        {
            "device_role": "satellite",
            "master_url": "http://user:secret@master.example:5001",
        },
        {
            "device_role": "satellite",
            "master_url": "http://master.example:5001?token=secret",
        },
        {
            "device_role": "satellite",
            "master_url": "http://master.example:5001#token-secret",
        },
        {"device_role": "satellite", "master_url": "ftp://master.example"},
        {"device_role": "satellite", "master_url": "not a URL"},
    ]
    javascript = f"""
"use strict";
global.window = {{location: {{origin: "http://satellite.local"}}}};
{function_source}
const configs = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(configs.map(albumRequestMasterScope)));
"""
    completed = subprocess.run(
        [node, "-e", javascript, json.dumps(configs)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        "master:https://master.example/base",
        "",
        "",
        "",
        "",
        "",
    ]

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


def test_healthz_bypasses_setup_gate(client, monkeypatch, tmp_path):
    # Pre-config liveness probe must not 302 to /setup, and must return
    # 200 so the Tauri desktop bootstrap can distinguish "alive but
    # unconfigured" from "backend down" via the body, not the status.
    monkeypatch.setattr('web_server.CONFIG_FILE', str(tmp_path / 'missing.json'))
    res = client.get('/api/healthz', follow_redirects=False)
    assert res.status_code == 200
    assert res.get_json() == {"ok": False, "initialized": False}

def test_artist_info_returns_summary_when_client_has_one(client, mock_config):
    fake = {
        "summary": "Beck is an American musician.",
        "source_url": "https://en.wikipedia.org/wiki/Beck",
        "image_url": None,
        "title": "Beck",
    }
    with patch('src.wikipedia_client.get_artist_summary', return_value=fake) as mock_get:
        res = client.get('/api/library/artists/Beck/info')

    assert res.status_code == 200
    body = res.get_json()
    assert body == {"success": True, "info": fake}
    mock_get.assert_called_once_with("Beck")


def test_artist_info_returns_success_false_on_miss(client, mock_config):
    with patch('src.wikipedia_client.get_artist_summary', return_value=None):
        res = client.get('/api/library/artists/Unknown%20Band/info')
    # Misses are 200/success:false (not HTTP 404) so the UI can render
    # an empty state without console noise.
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"success": False, "message": "no summary"}


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
        mock_db.get_current_timestamp.return_value = "2026-04-17 12:00:00"
        mock_db.get_catalog_since.return_value = [
            {"mbid": "m1", "title": "Song", "artist": "A", "updated_at": "2026-04-17 11:00:00"},
            {"mbid": "m2", "title": "Song2", "artist": "B", "updated_at": "2026-04-17 11:30:00"},
        ]

        res = client.get('/api/catalog')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["catalog_version"] == 2
    assert data["count"] == 2
    assert data["as_of"] == "2026-04-17 12:00:00"
    assert len(data["tracks"]) == 2
    mock_db.get_catalog_since.assert_called_once_with(None)


def test_get_catalog_with_since_filter(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_current_timestamp.return_value = "2026-04-17 13:00:00"
        mock_db.get_catalog_since.return_value = []

        res = client.get('/api/catalog?since=2026-04-17+12:00:00')

    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 0
    mock_db.get_catalog_since.assert_called_once_with("2026-04-17 12:00:00")


def test_get_artist_tags_delta_returns_grouped_snapshots(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_current_timestamp.return_value = "2026-06-03 12:00:00"
        mock_db.get_artist_tags_since.return_value = [{
            "artist_name": "Artist",
            "mbid": "artist-mbid",
            "fetched_at": "2026-06-03 11:00:00",
            "tags": [{"tag": "rock", "weight": 10}],
        }]

        res = client.get(
            '/api/artist-tags?since=2026-06-03+10:00:00'
        )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["as_of"] == "2026-06-03 12:00:00"
    assert data["count"] == 1
    assert data["artist_tags"][0]["artist_name"] == "Artist"
    mock_db.get_artist_tags_since.assert_called_once_with(
        "2026-06-03 10:00:00"
    )


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
            {
                "id": "rmb-1",
                "title": "Album One",
                "artist": "A featuring Guest",
                "primary_artist": None,
                "credited_artists": ["A", "A featuring Guest"],
                "track_count": 10,
                "cover_path": "/m/1.flac",
            },
            {
                "id": "Y|X",
                "title": "Y",
                "artist": "X",
                "primary_artist": "X",
                "credited_artists": ["X"],
                "track_count": 5,
                "cover_path": "/m/2.flac",
            },
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
    assert data["albums"][0]["artist"] == "A featuring Guest"
    assert data["albums"][0]["primary_artist"] is None
    assert data["albums"][0]["credited_artists"] == [
        "A",
        "A featuring Guest",
    ]


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


def test_library_tracks_filters_rows_master_cannot_stream(client, mock_config):
    mock_config._config = {"master_url": "http://master.example"}
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_all_tracks.return_value = [
            {"mbid": "t-dead", "title": "N", "artist": "A", "album": "Al",
             "track_number": 3, "disc_number": 1, "album_id": "rmb",
             "local_path": None, "dap_path": None, "master_streamable": 0},
        ]
        res = client.get('/api/library/tracks')

    assert res.status_code == 200
    assert res.get_json()["tracks"] == []


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
            {"playlist_id": "pl-1", "name": "Alpha", "track_count": 12, "updated_at": "2026-04-20 10:00:00", "smart_rules": None},
            {"playlist_id": "pl-2", "name": "Bravo", "track_count": 0, "updated_at": "2026-04-20 11:00:00", "smart_rules": None},
        ]
        res = client.get('/api/library/playlists')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert [p["name"] for p in data["playlists"]] == ["Alpha", "Bravo"]
    assert data["playlists"][0]["track_count"] == 12
    # Static playlists surface smart_rules as null so the client can
    # branch on truthy / falsy without a separate "is_smart" flag.
    assert data["playlists"][0]["smart_rules"] is None


def test_library_playlists_decodes_smart_rules_json(client, mock_config):
    # Stored as a JSON string in the column; the GET endpoint decodes it
    # so the client doesn't have to JSON.parse a field nested in JSON.
    with patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_playlists_with_counts.return_value = [
            {
                "playlist_id": "pl-smart",
                "name": "Smart",
                "track_count": 0,
                "updated_at": "2026-04-26 09:00:00",
                "smart_rules": '{"match":"all","rules":[{"field":"artist","op":"contains","value":"beatles"}]}',
            },
        ]
        res = client.get('/api/library/playlists')
    rules = res.get_json()["playlists"][0]["smart_rules"]
    assert rules == {
        "match": "all",
        "rules": [{"field": "artist", "op": "contains", "value": "beatles"}],
    }


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


def test_library_playlists_create_with_smart_rules(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.create_playlist.return_value = "smart-uuid"
        res = client.post(
            '/api/library/playlists',
            json={
                "name": "Beatles deep cuts",
                "smart_rules": {
                    "match": "all",
                    "rules": [{"field": "artist", "op": "contains", "value": "beatles"}],
                },
            },
        )

    assert res.status_code == 201
    data = res.get_json()
    assert data["playlist_id"] == "smart-uuid"
    args, kwargs = inst.create_playlist.call_args
    # Name is positional, smart_rules is keyword-passed as a JSON string.
    assert args[0] == "Beatles deep cuts"
    stored = json.loads(kwargs["smart_rules"])
    assert stored == {
        "match": "all",
        "rules": [{"field": "artist", "op": "contains", "value": "beatles"}],
    }


def test_library_playlists_create_rejects_bad_rules(client, mock_config):
    # Unknown field — coerce_ruleset must raise so the endpoint returns
    # 400 and never calls create_playlist with garbage.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        res = client.post(
            '/api/library/playlists',
            json={
                "name": "Bad",
                "smart_rules": {
                    "match": "all",
                    "rules": [{"field": "secret_column", "op": "equals", "value": "x"}],
                },
            },
        )
    assert res.status_code == 400
    assert "secret_column" in res.get_json()["message"]
    assert not inst.create_playlist.called


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


def test_library_playlist_update_sets_smart_rules(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_playlist.return_value = MagicMock()
        inst.update_playlist_smart_rules.return_value = True
        res = client.put(
            '/api/library/playlists/pl-1',
            json={
                "smart_rules": {
                    "match": "any",
                    "rules": [
                        {"field": "tag_score", "op": "gt", "value": 80},
                        {"field": "album", "op": "contains", "value": "live"},
                    ],
                },
            },
        )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["rules_changed"] is True
    args, _ = inst.update_playlist_smart_rules.call_args
    assert args[0] == "pl-1"
    stored = json.loads(args[1])
    assert stored["match"] == "any"
    assert len(stored["rules"]) == 2


def test_library_playlist_update_clears_smart_rules_with_null(client, mock_config):
    # Explicit null converts a smart playlist back to a static one —
    # serialize() returns None for None / empty rules, so the column
    # ends up NULL.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_playlist.return_value = MagicMock()
        inst.update_playlist_smart_rules.return_value = True
        res = client.put(
            '/api/library/playlists/pl-1',
            json={"smart_rules": None},
        )

    assert res.status_code == 200
    inst.update_playlist_smart_rules.assert_called_once_with("pl-1", None)


def test_library_playlist_update_rejects_tracks_and_rules_together(client, mock_config):
    # Mixing manual membership and rule-driven membership in one request
    # has no coherent semantics; reject up front rather than silently
    # dropping one of them.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        res = client.put(
            '/api/library/playlists/pl-1',
            json={
                "track_mbids": ["a"],
                "smart_rules": {
                    "match": "all",
                    "rules": [{"field": "artist", "op": "equals", "value": "x"}],
                },
            },
        )
    assert res.status_code == 400
    assert "mutually exclusive" in res.get_json()["message"]
    assert not inst.replace_playlist_membership.called
    assert not inst.update_playlist_smart_rules.called


def test_library_playlist_update_rejects_bad_rules(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        res = client.put(
            '/api/library/playlists/pl-1',
            json={
                "smart_rules": {
                    "match": "all",
                    "rules": [{"field": "artist", "op": "regex", "value": ".*"}],
                },
            },
        )
    assert res.status_code == 400
    # Validation runs before get_playlist, so we never even open the DB.
    assert not inst.get_playlist.called


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


def test_library_album_cover_proxies_master_when_no_local_path(client, mock_config):
    mock_config._config = {"master_url": "http://master.example/"}
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": "4",
            "Cache-Control": "public, max-age=86400",
        }
        upstream.iter_content.return_value = iter([b"JPEG"])
        mock_get.return_value = upstream

        res = client.get('/api/library/albums/Album%20One%7CArtist%2FName/cover')

    assert res.status_code == 200
    assert res.mimetype == "image/jpeg"
    assert res.data == b"JPEG"
    assert res.headers["Cache-Control"] == "public, max-age=86400"
    call = mock_get.call_args
    assert call.args[0] == (
        "http://master.example/api/library/albums/"
        "Album%20One%7CArtist%2FName/cover"
    )
    assert call.kwargs["stream"] is True
    assert "Authorization" not in call.kwargs["headers"]
    upstream.close.assert_called_once_with()


def test_library_album_cover_uses_disk_cache_before_master(
    client,
    mock_config,
    tmp_path,
):
    from src.artwork_cache import artwork_cache_for_database

    mock_config.db_path = str(tmp_path / "library.db")
    mock_config._config = {"master_url": "http://master.example"}
    cache = artwork_cache_for_database(mock_config.db_path)
    assert cache is not None
    assert cache.store("album-1", b"CACHED", "image/jpeg")

    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None

        res = client.get('/api/library/albums/album-1/cover')

    assert res.status_code == 200
    assert res.mimetype == "image/jpeg"
    assert res.data == b"CACHED"
    mock_get.assert_not_called()


def test_library_album_cover_caches_complete_master_response(
    client,
    mock_config,
    tmp_path,
):
    from src.artwork_cache import CachedArtwork, artwork_cache_for_database

    mock_config.db_path = str(tmp_path / "library.db")
    mock_config._config = {"master_url": "http://master.example"}
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "image/png"}
        upstream.iter_content.return_value = iter([b"PN", b"G"])
        mock_get.return_value = upstream

        first = client.get('/api/library/albums/album-1/cover')

    assert first.status_code == 200
    assert first.data == b"PNG"
    cache = artwork_cache_for_database(mock_config.db_path)
    assert cache is not None
    assert cache.load("album-1") == CachedArtwork(b"PNG", "image/png")

    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get', side_effect=AssertionError("must use cache")):
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None

        second = client.get('/api/library/albums/album-1/cover')

    assert second.status_code == 200
    assert second.data == b"PNG"


def test_library_album_cover_proxies_when_local_file_has_no_art(client, mock_config):
    mock_config._config = {"master_url": "http://master.example"}
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.cover_art.extract_cover', return_value=None), \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = "/m/1.flac"
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "image/png"}
        upstream.iter_content.return_value = iter([b"PNG"])
        mock_get.return_value = upstream

        res = client.get('/api/library/albums/rmb-1/cover')

    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert res.data == b"PNG"


def test_library_album_cover_proxy_forwards_bearer_token(client, mock_config):
    mock_config._config = {
        "master_url": "http://master.example",
        "api_token": "tok",
    }
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get') as mock_get:
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "image/jpeg"}
        upstream.iter_content.return_value = iter([b"X"])
        mock_get.return_value = upstream

        res = client.get(
            '/api/library/albums/rmb-1/cover',
            headers={"Authorization": "Bearer tok"},
        )

    assert res.status_code == 200
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_library_album_cover_proxy_returns_502_on_upstream_failure(client, mock_config):
    mock_config._config = {"master_url": "http://master.example"}
    import requests as _r
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get', side_effect=_r.ConnectionError("boom")):
        MockDB.return_value.__enter__.return_value.get_album_cover_path.return_value = None
        res = client.get('/api/library/albums/rmb-1/cover')

    assert res.status_code == 502


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


def test_library_album_tracks_prefers_master_playable_rows(client, mock_config):
    mock_config._config = {
        "master_url": "http://master.example/",
        "api_token": "tok",
    }
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {"Content-Type": "application/json"}
    upstream.content = json.dumps({
        "success": True,
        "tracks": [
            {"mbid": "playable", "title": "Track 1", "track_number": 1},
        ],
    }).encode()
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.get', return_value=upstream) as mock_get:
        res = client.get(
            '/api/library/albums/Album%20One%7CArtist%2FName/tracks',
            headers={"Authorization": "Bearer tok"},
        )

    assert res.status_code == 200
    assert [row["mbid"] for row in res.get_json()["tracks"]] == ["playable"]
    assert mock_get.call_args.args[0] == (
        "http://master.example/api/library/albums/"
        "Album%20One%7CArtist%2FName/tracks"
    )
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    MockDB.assert_not_called()
    upstream.close.assert_called_once_with()


def test_library_album_tracks_uses_replica_when_master_offline(client, mock_config):
    mock_config._config = {"master_url": "http://master.example"}
    import requests as _r
    with patch('requests.get', side_effect=_r.ConnectionError("offline")), \
         patch('web_server.DatabaseManager') as MockDB:
        MockDB.return_value.__enter__.return_value.list_album_tracks.return_value = [
            {"mbid": "local", "title": "Track 1", "artist": "A", "album": "B",
             "track_number": 1, "disc_number": 1, "local_path": "/m/a.flac",
             "dap_path": None},
        ]
        res = client.get('/api/library/albums/rmb-1/tracks')

    assert res.status_code == 200
    assert [row["mbid"] for row in res.get_json()["tracks"]] == ["local"]


def test_library_album_tracks_uses_replica_and_closes_on_master_5xx(
    client, mock_config
):
    mock_config._config = {"master_url": "http://master.example"}

    class FailedUpstream:
        status_code = 503
        headers = {"Content-Type": "application/json"}

        def __init__(self):
            self.close = MagicMock()

        @property
        def content(self):
            raise AssertionError("5xx fallback must not buffer the response body")

    upstream = FailedUpstream()
    with patch("requests.get", return_value=upstream), \
         patch("web_server.DatabaseManager") as MockDB:
        MockDB.return_value.__enter__.return_value.list_album_tracks.return_value = [
            {
                "mbid": "local",
                "title": "Track 1",
                "artist": "A",
                "album": "B",
                "track_number": 1,
                "disc_number": 1,
                "local_path": "/m/a.flac",
                "dap_path": None,
            },
        ]
        res = client.get("/api/library/albums/rmb-1/tracks")

    assert res.status_code == 200
    assert [row["mbid"] for row in res.get_json()["tracks"]] == ["local"]
    upstream.close.assert_called_once_with()


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
    # Page extends _layout.html and is the active row in the shared nav.
    assert b"app-sidebar" in res.data
    assert b'href="/fleet" class="app-nav-item active"' in res.data


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
    # Legacy files are presented through canonical role semantics.
    assert data["config"]["device_role"] == "master"
    assert data["config"]["is_master"] is True
    assert data["config"]["lidarr_acquisition_handoff_enabled"] is False
    assert "slsk_password" in data["secret_keys"]
    # bool_keys + groups shipped so the desktop Settings dialog reads
    # them from the single config_keys.py source rather than drifting
    # like the web dashboard's hardcoded JS copy did.
    assert "report_inventory_to_host" in data["bool_keys"]
    assert "lidarr_acquisition_handoff_enabled" in data["bool_keys"]
    assert "lidarr_acquisition_handoff_enabled" in data["editable_keys"]
    assert "is_master" not in data["editable_keys"]
    assert "is_master" not in data["bool_keys"]
    assert isinstance(data["groups"], list) and data["groups"]
    assert {"label", "keys"} <= set(data["groups"][0].keys())
    assert all(
        "is_master" not in group["keys"] for group in data["groups"]
    )


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


def test_post_config_role_flip_resynchronizes_legacy_bool_and_schedulers(
    client, mock_config, tmp_path, monkeypatch
):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    with patch("web_server._start_release_watcher") as release_restart, \
         patch("web_server._start_library_maintenance_scheduler") as maintenance_restart:
        res = client.post("/api/config", json={
            "device_role": "satellite",
            # No longer editable; it must not override the canonical role.
            "is_master": True,
        })

    assert res.status_code == 200
    assert res.get_json()["changed"] == ["device_role"]
    saved = json.loads(cfg_path.read_text())
    assert saved["device_role"] == "satellite"
    assert saved["is_master"] is False
    release_restart.assert_called_once_with()
    maintenance_restart.assert_called_once_with(run_on_startup=False)


def test_post_config_ignores_legacy_bool_and_migrates_roleless_file(
    client, mock_config, tmp_path, monkeypatch
):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    res = client.post("/api/config", json={"is_master": False})

    assert res.status_code == 200
    assert res.get_json()["changed"] == []
    saved = json.loads(cfg_path.read_text())
    assert saved["device_role"] == "master"
    assert saved["is_master"] is True


def test_post_config_rejects_invalid_device_role(
    client, mock_config, tmp_path, monkeypatch
):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    before = cfg_path.read_text()

    res = client.post("/api/config", json={"device_role": "overlord"})

    assert res.status_code == 400
    assert "device_role" in res.get_json()["message"]
    assert cfg_path.read_text() == before


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


def test_maintenance_scheduler_reloads_without_startup_run(
    client, mock_config, tmp_path, monkeypatch
):
    _config_roundtrip_fixtures(tmp_path, monkeypatch)
    with patch('web_server._start_library_maintenance_scheduler') as restart:
        res = client.post('/api/config', json={
            'library_maintenance_interval_seconds': 0,
        })
    assert res.status_code == 200
    restart.assert_called_once_with(run_on_startup=False)


def test_browser_token_rotation_refreshes_auth_cookie(
    client, mock_config, tmp_path, monkeypatch
):
    cfg_path = _config_roundtrip_fixtures(tmp_path, monkeypatch)
    raw = json.loads(cfg_path.read_text())
    raw['api_token'] = 'old-secret'
    cfg_path.write_text(json.dumps(raw))
    mock_config._config = {'api_token': 'old-secret'}

    def reload_mock():
        mock_config._config = json.loads(cfg_path.read_text())

    mock_config._load_config.side_effect = reload_mock
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    login = client.post('/auth', data={'token': 'old-secret', 'next': '/'})
    assert login.status_code == 302

    changed = client.post(
        '/api/config',
        json={'api_token': 'new-secret'},
        headers={'Origin': 'http://localhost'},
    )
    assert changed.status_code == 200
    assert 'HttpOnly' in changed.headers['Set-Cookie']
    assert 'new-secret' in changed.headers['Set-Cookie']
    assert client.get('/').status_code == 200


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


def test_sync_state_returns_all_cursors(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_db.get_sync_state.side_effect = lambda key: {
            "last_catalog_sync": "2026-04-19 10:00:00",
            "last_artist_tags_sync": "2026-04-19 10:00:30",
            "last_playlist_sync": "2026-04-19 10:01:00",
            "last_playlist_push": None,
            "last_lyrics_sync": "2026-04-19 10:01:30",
            "last_inventory_report": "2026-04-19 10:02:00",
            "last_contribute": "2026-04-19 10:03:00",
        }[key]
        res = client.get('/api/sync/state')
    data = res.get_json()
    assert data["success"] is True
    assert data["state"]["catalog_pull"] == "2026-04-19 10:00:00"
    assert data["state"]["artist_tags_pull"] == "2026-04-19 10:00:30"
    assert data["state"]["playlist_pull"] == "2026-04-19 10:01:00"
    assert data["state"]["playlist_push"] is None
    assert data["state"]["lyrics_pull"] == "2026-04-19 10:01:30"
    assert data["state"]["inventory_report"] == "2026-04-19 10:02:00"
    assert data["state"]["contribute"] == "2026-04-19 10:03:00"


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
        mock_db.get_current_timestamp.return_value = "2026-04-18 12:00:00"
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
        mock_db.get_current_timestamp.return_value = "2026-04-18 12:00:00"
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


def test_forward_suggestions_uses_configured_master_and_token(client, mock_config):
    values = {
        "master_url": "http://master.local:5001/",
        "api_token": "shared-secret",
    }
    mock_config.get.side_effect = lambda key, default=None: values.get(key, default)
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "success": True, "received": 1, "queued": 1, "skipped": 0,
    }
    with patch('requests.post', return_value=response) as post:
        res = client.post('/api/suggestions/forward', json={
            "items": [{"artist": "Radiohead", "title": "Idioteque"}],
        })
    assert res.status_code == 200
    assert res.get_json()["queued"] == 1
    post.assert_called_once_with(
        "http://master.local:5001/api/suggestions",
        json={"items": [{"artist": "Radiohead", "title": "Idioteque"}]},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer shared-secret",
        },
        timeout=(5, 30),
    )


def test_forward_suggestions_requires_master_url(client, mock_config):
    mock_config.get.side_effect = lambda key, default=None: default
    res = client.post('/api/suggestions/forward', json={"items": []})
    assert res.status_code == 409
    assert "master_url" in res.get_json()["message"]


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


@pytest.fixture
def _browser_cookie_client(client, _token_config, monkeypatch):
    """Log the Flask test browser in through the real HttpOnly-cookie flow."""
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    login = client.post('/auth', data={
        'token': 'secret-xyz', 'next': '/',
    }, follow_redirects=False)
    assert login.status_code == 302
    return client


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


def test_api_accepts_query_token_for_media_style_get(client, _token_config):
    """Audio/img elements cannot attach Authorization, so GET supports a token query."""
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('shutil.disk_usage') as mock_du:
        mock_instance = MockDB.return_value.__enter__.return_value
        mock_instance.get_library_stats.return_value = {
            'tracks': 1, 'artists': 1, 'albums': 1, 'playlists': 0,
            'incomplete_albums': 0,
        }
        mock_du.return_value = (100*1024**3, 50*1024**3, 50*1024**3)
        res = client.get('/api/stats?token=secret-xyz')
    assert res.status_code == 200


def test_api_rejects_query_token_for_post(client, _token_config):
    res = client.post('/api/suggestions?token=secret-xyz', json={"items": []})
    assert res.status_code == 401


def test_protected_page_never_embeds_configured_api_token(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    res = client.get('/', follow_redirects=False)
    assert res.status_code == 302
    assert '/auth' in res.headers['Location']
    assert b'secret-xyz' not in res.data

    login = client.get('/auth')
    assert login.status_code == 200
    assert b'secret-xyz' not in login.data


def test_browser_login_cookie_authenticates_ui_and_api(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    login = client.post('/auth', data={
        'token': 'secret-xyz', 'next': '/',
    }, follow_redirects=False)
    assert login.status_code == 302
    cookie = login.headers['Set-Cookie']
    assert 'HttpOnly' in cookie
    assert 'SameSite=Strict' in cookie

    page = client.get('/')
    assert page.status_code == 200
    assert b'secret-xyz' not in page.data

    with patch('web_server.DatabaseManager') as MockDB, \
         patch('shutil.disk_usage') as mock_du:
        MockDB.return_value.__enter__.return_value.get_library_stats.return_value = {
            'tracks': 0, 'artists': 0, 'albums': 0, 'playlists': 0,
            'incomplete_albums': 0,
        }
        mock_du.return_value = (1, 1, 0)
        api = client.get('/api/stats')
    assert api.status_code == 200


def test_proxy_trust_requires_explicit_literal_one():
    assert web_server._trust_one_proxy_hop({}) is False
    assert web_server._trust_one_proxy_hop({"DAPMANAGER_TRUST_PROXY": ""}) is False
    assert web_server._trust_one_proxy_hop({"DAPMANAGER_TRUST_PROXY": "true"}) is False
    assert web_server._trust_one_proxy_hop({"DAPMANAGER_TRUST_PROXY": "1"}) is True


def test_https_proxy_login_sets_secure_cookie(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    original_wsgi_app = web_server.app.wsgi_app
    web_server.app.wsgi_app = web_server._proxy_aware_wsgi_app(original_wsgi_app)
    try:
        login = client.post(
            '/auth',
            data={'token': 'secret-xyz', 'next': '/satellite'},
            headers={
                'X-Forwarded-Proto': 'https',
                'X-Forwarded-Host': 'master.example.ts.net:10000',
                'X-Forwarded-Port': '10000',
            },
            follow_redirects=False,
        )
    finally:
        web_server.app.wsgi_app = original_wsgi_app

    assert login.status_code == 302
    assert login.headers['Location'] == '/satellite'
    cookie = login.headers['Set-Cookie']
    assert 'Secure' in cookie
    assert 'HttpOnly' in cookie
    assert 'SameSite=Strict' in cookie


def test_cookie_mutation_accepts_same_https_origin_behind_one_proxy(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    original_wsgi_app = web_server.app.wsgi_app
    web_server.app.wsgi_app = web_server._proxy_aware_wsgi_app(original_wsgi_app)
    forwarded = {
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Host': 'master.example.ts.net:10000',
        'X-Forwarded-Port': '10000',
    }
    try:
        login = client.post(
            '/auth',
            data={'token': 'secret-xyz', 'next': '/'},
            headers=forwarded,
            follow_redirects=False,
        )
        assert login.status_code == 302

        response = client.post(
            '/api/suggestions',
            json={'items': []},
            headers={
                **forwarded,
                'Origin': 'https://master.example.ts.net:10000',
            },
        )
    finally:
        web_server.app.wsgi_app = original_wsgi_app

    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_cookie_mutation_still_rejects_cross_origin_behind_one_proxy(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    original_wsgi_app = web_server.app.wsgi_app
    web_server.app.wsgi_app = web_server._proxy_aware_wsgi_app(original_wsgi_app)
    forwarded = {
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Host': 'master.example.ts.net:10000',
        'X-Forwarded-Port': '10000',
    }
    try:
        login = client.post(
            '/auth',
            data={'token': 'secret-xyz', 'next': '/'},
            headers=forwarded,
            follow_redirects=False,
        )
        assert login.status_code == 302

        response = client.post(
            '/api/suggestions',
            json={'items': []},
            headers={**forwarded, 'Origin': 'https://evil.example'},
        )
    finally:
        web_server.app.wsgi_app = original_wsgi_app

    assert response.status_code == 403
    assert 'same-origin' in response.get_json()['message']


def test_ui_query_token_bootstraps_cookie_then_strips_secret(
    client, _token_config, monkeypatch
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    res = client.get('/?token=secret-xyz', follow_redirects=False)
    assert res.status_code == 302
    assert res.headers['Location'].endswith('/')
    assert 'token=' not in res.headers['Location']
    assert 'HttpOnly' in res.headers['Set-Cookie']


def test_api_status_is_exempt_from_token(client, _token_config):
    """Health checks must not require the token."""
    res = client.get('/api/status')
    assert res.status_code == 200


def test_api_status_download_scope_requires_token_before_proxying(
    client, _token_config,
):
    _token_config.is_master = False
    _token_config.master_url = "http://master.local:5001"
    _token_config.get.side_effect = lambda key, default=None: (
        "secret-xyz" if key == "api_token" else default
    )
    with patch(
        "web_server.album_download_request_service.forward_master_json"
    ) as forward:
        res = client.get("/api/status?scope=downloads")

    assert res.status_code == 401
    assert res.get_json()["message"] == "missing bearer token"
    forward.assert_not_called()


def test_api_status_download_scope_proxies_after_token_authentication(
    client, _token_config,
):
    from src.services.album_download_request_service import AlbumRequestResult

    _token_config.is_master = False
    _token_config.master_url = "http://master.local:5001"
    _token_config.get.side_effect = lambda key, default=None: (
        "secret-xyz" if key == "api_token" else default
    )
    proxied = AlbumRequestResult({"running": True, "message": "Downloading"})
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward:
        res = client.get(
            "/api/status?scope=downloads",
            headers={"Authorization": "Bearer secret-xyz"},
        )

    assert res.status_code == 200
    assert res.get_json() == {"running": True, "message": "Downloading"}
    forward.assert_called_once_with(
        "http://master.local:5001",
        "GET",
        "/api/status",
        api_token="secret-xyz",
    )


def test_api_open_mode_when_token_empty(client, mock_config):
    """Existing behavior: no token set => no auth enforced."""
    res = client.get('/api/status')
    assert res.status_code == 200


def test_api_token_blocks_post_routes_too(client, _token_config):
    res = client.post('/api/suggestions', json={"items": []})
    assert res.status_code == 401


@pytest.mark.parametrize("origin", [
    "http://attacker.localhost",  # same-site, different host origin
    "https://localhost",          # same host, different scheme origin
    "https://evil.example",       # fully cross-site origin
])
def test_cookie_authenticated_post_rejects_cross_origin(
    _browser_cookie_client, origin,
):
    res = _browser_cookie_client.post(
        '/api/suggestions',
        json={"items": []},
        headers={"Origin": origin},
    )
    assert res.status_code == 403
    assert "same-origin" in res.get_json()["message"]


def test_cookie_authenticated_post_rejects_absent_provenance(
    _browser_cookie_client,
):
    res = _browser_cookie_client.post(
        '/api/suggestions', json={"items": []},
    )
    assert res.status_code == 403


def test_cookie_authenticated_post_accepts_same_origin(
    _browser_cookie_client,
):
    res = _browser_cookie_client.post(
        '/api/suggestions',
        json={"items": []},
        headers={"Origin": "http://localhost"},
    )
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_cookie_authenticated_post_accepts_same_origin_referer(
    _browser_cookie_client,
):
    res = _browser_cookie_client.post(
        '/api/suggestions',
        json={"items": []},
        headers={"Referer": "http://localhost/satellite"},
    )
    assert res.status_code == 200


def test_bearer_authenticated_tauri_post_does_not_require_web_origin(
    client, _token_config, monkeypatch,
):
    monkeypatch.setattr('web_server.config_exists', lambda: True)
    res = client.post(
        '/api/suggestions',
        json={"items": []},
        headers={
            "Authorization": "Bearer secret-xyz",
            "Origin": "tauri://localhost",
        },
    )
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    assert res.headers["Access-Control-Allow-Origin"] == "tauri://localhost"


def test_tauri_preflight_is_narrowly_allowed(client, _token_config):
    res = client.options('/api/suggestions', headers={
        'Origin': 'tauri://localhost',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'authorization,content-type',
    })
    assert res.status_code == 204
    assert res.headers['Access-Control-Allow-Origin'] == 'tauri://localhost'
    assert 'Authorization' in res.headers['Access-Control-Allow-Headers']


def test_unknown_origin_does_not_receive_cors_access(client, _token_config):
    res = client.options('/api/suggestions', headers={
        'Origin': 'https://evil.example',
        'Access-Control-Request-Method': 'POST',
    })
    assert res.status_code == 401
    assert 'Access-Control-Allow-Origin' not in res.headers


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


def test_tag_identify_translates_identifier_exception(client, _tag_config):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = "/music/song.flac"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch(
            'src.tag_service.identify_file',
            side_effect=RuntimeError("fingerprint failed"),
        ):
            res = client.post('/api/tag/identify/m1')

    assert res.status_code == 500
    assert res.get_json() == {
        "success": False,
        "message": "fingerprint failed",
    }


def test_tag_identify_does_not_translate_current_tag_failure(
    client, _tag_config,
):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.local_path = "/music/song.flac"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.identify_file', return_value=None), \
             patch(
                 'src.tag_service.read_current_tags',
                 side_effect=RuntimeError("unreadable"),
             ), \
             pytest.raises(RuntimeError, match="unreadable"):
            client.post('/api/tag/identify/m1')


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


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ValueError("unsupported format"), 400),
        (RuntimeError("write failed"), 500),
    ],
)
def test_tag_apply_translates_only_file_write_errors(
    client, mock_config, error, expected_status,
):
    with patch('web_server.DatabaseManager') as MockDB:
        mock_db = MockDB.return_value.__enter__.return_value
        mock_track = MagicMock()
        mock_track.mbid = "m1"
        mock_track.local_path = "/music/song.flac"
        mock_db.get_track_by_mbid.return_value = mock_track

        with patch('src.tag_service.write_tags', side_effect=error):
            res = client.post(
                '/api/tag/apply/m1',
                json={"meta": {"title": "New"}},
            )

    assert res.status_code == expected_status
    assert res.get_json()["message"] == str(error)
    mock_db.add_or_update_track.assert_not_called()
    mock_db.set_track_tag_tier.assert_not_called()


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


def test_request_download_proxies_from_satellite(client, mock_config):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.return_value = "token"
    proxied = AlbumRequestResult({"success": True, "queued": True, "item_id": 9})
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward:
        res = client.post('/api/download/request', json={"search_query": "x"})

    assert res.status_code == 200
    assert res.get_json()["item_id"] == 9
    forward.assert_called_once_with(
        "http://master.local:5001",
        "POST",
        "/api/download/request",
        api_token="token",
        json_body={"search_query": "x"},
    )


@pytest.mark.parametrize(
    ("method", "route", "upstream_path", "json_body"),
    [
        ("GET", "/api/status?scope=downloads", "/api/status", None),
        (
            "POST",
            "/api/catalog/queue-download",
            "/api/catalog/queue-download",
            {"mbids": ["recording-1"]},
        ),
        ("GET", "/api/downloads/list", "/api/downloads/list", None),
        ("POST", "/api/downloads/42/retry", "/api/downloads/42/retry", None),
        ("DELETE", "/api/downloads/42", "/api/downloads/42", None),
        (
            "POST",
            "/api/downloads/clear-completed",
            "/api/downloads/clear-completed",
            None,
        ),
    ],
)
def test_satellite_download_queue_surface_proxies_to_master(
    client,
    mock_config,
    method,
    route,
    upstream_path,
    json_body,
):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.return_value = "master-token"
    proxied = AlbumRequestResult({"success": True, "running": True, "items": []})
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward, patch("web_server.DatabaseManager") as database:
        request_kwargs = {"json": json_body} if json_body is not None else {}
        res = client.open(route, method=method, **request_kwargs)

    assert res.status_code == 200
    assert res.get_json()["success"] is True
    expected_kwargs = {"api_token": "master-token"}
    if json_body is not None:
        expected_kwargs["json_body"] = json_body
    forward.assert_called_once_with(
        "http://master.local:5001",
        method,
        upstream_path,
        **expected_kwargs,
    )
    database.assert_not_called()


def test_unscoped_satellite_status_remains_local_for_sync_tasks(
    client, mock_config
):
    mock_config.is_master = False
    with patch(
        "web_server.album_download_request_service.forward_master_json"
    ) as forward:
        res = client.get("/api/status")

    assert res.status_code == 200
    assert "running" in res.get_json()
    forward.assert_not_called()


def test_album_download_search_returns_musicbrainz_release_candidates(
    client, mock_config
):
    mock_config.is_master = True
    response = {
        "release-list": [{
            "id": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
            "title": "Album",
            "artist-credit": [{"artist": {"name": "Artist"}}],
            "track-count": 9,
            "release-group": {"primary-type": "Album"},
        }]
    }
    with patch(
        "src.musicbrainz_client.search_releases",
        return_value=response,
    ) as search:
        res = client.get("/api/download/albums/search?q=Artist+-+Album")

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["ambiguous"] is False
    assert body["candidates"][0]["track_count"] == 9
    search.assert_called_once_with(
        limit=8,
        artist="Artist",
        release="Album",
        primarytype="album",
    )


def test_album_download_request_rechecks_musicbrainz_and_queues_canonical_mbid(
    client, mock_config
):
    mock_config.is_master = True
    mock_config.get.side_effect = lambda key, default=None: {
        "auto_tag_downloads": True,
        "acoustid_api_key": "acoustid-test-key",
    }.get(key, default)
    release_id = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    verified = {
        "release": {
            "id": release_id.upper(),
            "title": "Verified Album",
            "artist-credit": [{"artist": {"name": "Verified Artist"}}],
            "medium-list": [{
                "position": 1,
                "track-count": 2,
                "track-list": [
                    {
                        "id": "10000000-0000-4000-8000-000000000001",
                        "position": 1,
                        "number": "1",
                        "title": "Track 1",
                        "recording": {
                            "id": "00000000-0000-4000-8000-000000000001",
                            "title": "Track 1",
                        },
                    },
                    {
                        "id": "10000000-0000-4000-8000-000000000002",
                        "position": 2,
                        "number": "2",
                        "title": "Track 2",
                        "recording": {
                            "id": "00000000-0000-4000-8000-000000000002",
                            "title": "Track 2",
                        },
                    },
                ],
            }],
            "release-group": {"primary-type": "Album"},
        }
    }
    with patch(
        "src.musicbrainz_client.get_release_by_id",
        return_value=verified,
    ), patch("web_server.DatabaseManager") as MockDB:
        db = MockDB.return_value.__enter__.return_value
        db.get_album_download_request_by_release.return_value = None
        db.count_local_release_tracks.return_value = 0
        db.create_download_and_album_request.return_value = (41, 6)
        db.get_album_download_request.return_value = {
            "id": 6,
            "queue_item_id": 41,
            "release_mbid": release_id,
            "artist": "Verified Artist",
            "title": "Verified Album",
            "track_count": 2,
            "stage": "queued",
            "detail": "Waiting",
            "completed_tracks": 0,
            "queue_status": "pending",
        }
        res = client.post(
            "/api/download/albums/request",
            json={
                "release_mbid": release_id,
                "title": "Client cannot override this",
                "track_count": 999,
            },
        )

    assert res.status_code == 200
    body = res.get_json()
    assert body["request"]["release_mbid"] == release_id
    assert body["request"]["track_count"] == 2
    queued = db.create_download_and_album_request.call_args.kwargs
    assert queued["search_query"] == "::ALBUM:: Verified Artist - Verified Album"
    assert queued["release_mbid"] == release_id


@pytest.mark.parametrize(
    ("settings", "expected_message"),
    [
        (
            {"auto_tag_downloads": False, "acoustid_api_key": "configured"},
            "auto_tag_downloads",
        ),
        (
            {"auto_tag_downloads": True, "acoustid_api_key": "   "},
            "acoustid_api_key",
        ),
    ],
)
def test_album_download_request_rejects_unusable_tagging_configuration_before_lookup(
    client, mock_config, settings, expected_message,
):
    mock_config.is_master = True
    mock_config.get.side_effect = lambda key, default=None: settings.get(key, default)
    with patch("src.musicbrainz_client.get_release_by_id") as resolve, patch(
        "web_server.DatabaseManager"
    ) as database:
        res = client.post(
            "/api/download/albums/request",
            json={"release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36"},
        )

    assert res.status_code == 409
    body = res.get_json()
    assert body["success"] is False
    assert expected_message in body["message"]
    assert "master" in body["message"]
    resolve.assert_not_called()
    database.assert_not_called()


def test_album_download_request_satellite_safely_relays_master_config_error(
    client, mock_config,
):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.side_effect = lambda key, default=None: (
        "master-token" if key == "api_token" else default
    )
    payload = {
        "success": False,
        "message": (
            "Verified album downloads require acoustid_api_key to be "
            "configured on the master."
        ),
    }
    proxied = AlbumRequestResult(payload, status_code=409)
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward, patch("src.musicbrainz_client.get_release_by_id") as resolve:
        res = client.post(
            "/api/download/albums/request",
            json={"release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36"},
        )

    assert res.status_code == 409
    assert res.get_json() == payload
    assert "master-token" not in res.get_data(as_text=True)
    forward.assert_called_once_with(
        "http://master.local:5001",
        "POST",
        "/api/download/albums/request",
        api_token="master-token",
        json_body={
            "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        },
    )
    resolve.assert_not_called()


def test_album_download_routes_proxy_from_satellite_with_server_token(
    client, mock_config
):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.side_effect = lambda key, default=None: (
        "master-token" if key == "api_token" else default
    )
    proxied = AlbumRequestResult({
        "success": True,
        "request": {"id": 12, "stage": "downloading"},
    })
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward:
        res = client.get("/api/download/albums/requests/12")

    assert res.status_code == 200
    assert res.get_json()["request"]["stage"] == "downloading"
    forward.assert_called_once_with(
        "http://master.local:5001",
        "GET",
        "/api/download/albums/requests/12",
        api_token="master-token",
    )
    assert res.headers["Cache-Control"] == "no-store"


def test_album_download_request_list_proxies_for_browser_reconciliation(
    client, mock_config
):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.side_effect = lambda key, default=None: (
        "master-token" if key == "api_token" else default
    )
    proxied = AlbumRequestResult({
        "success": True,
        "requests": [{"id": 12, "stage": "downloading"}],
    })
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward:
        res = client.get("/api/download/albums/requests")

    assert res.status_code == 200
    assert res.get_json()["requests"][0]["id"] == 12
    assert res.headers["Cache-Control"] == "no-store"
    forward.assert_called_once_with(
        "http://master.local:5001",
        "GET",
        "/api/download/albums/requests",
        api_token="master-token",
    )


def test_run_download_queue_proxies_from_satellite_with_server_token(
    client, mock_config
):
    from src.services.album_download_request_service import AlbumRequestResult

    mock_config.is_master = False
    mock_config.master_url = "http://master.local:5001"
    mock_config.get.side_effect = lambda key, default=None: (
        "master-token" if key == "api_token" else default
    )
    proxied = AlbumRequestResult({"success": True, "message": "Task started."})
    with patch(
        "web_server.album_download_request_service.forward_master_json",
        return_value=proxied,
    ) as forward:
        res = client.post("/api/download")

    assert res.status_code == 200
    assert res.get_json()["success"] is True
    forward.assert_called_once_with(
        "http://master.local:5001",
        "POST",
        "/api/download",
        api_token="master-token",
    )


def test_request_download_rejects_empty_query(client, mock_config):
    mock_config.is_master = True
    res = client.post('/api/download/request', json={"search_query": "   "})
    assert res.status_code == 400
    assert "search_query" in res.get_json()["message"]


def test_request_download_rejects_missing_body(client, mock_config):
    mock_config.is_master = True
    res = client.post('/api/download/request', json={})
    assert res.status_code == 400


# --- Play events / listening stats ---


def test_record_play_returns_event_id(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.record_play_event.return_value = 42
        res = client.post(
            '/api/library/plays',
            json={"mbid": "abc", "source": "desktop"},
        )

    assert res.status_code == 201
    assert res.get_json() == {"success": True, "event_id": 42}
    inst.record_play_event.assert_called_once_with(
        "abc", source="desktop", listened_ms=None,
    )


def test_record_play_accepts_omitted_source(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.record_play_event.return_value = 1
        res = client.post('/api/library/plays', json={"mbid": "xyz"})

    assert res.status_code == 201
    inst.record_play_event.assert_called_once_with(
        "xyz", source=None, listened_ms=None,
    )


def test_record_play_rejects_blank_mbid(client, mock_config):
    res = client.post('/api/library/plays', json={"mbid": "  "})
    assert res.status_code == 400
    assert "mbid" in res.get_json()["message"]


def test_record_play_rejects_non_string_source(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        res = client.post(
            '/api/library/plays',
            json={"mbid": "abc", "source": 123},
        )
    assert res.status_code == 400
    assert not inst.record_play_event.called


def test_play_stats_aggregates_db_helpers(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 7
        inst.listening_time_since.return_value = 3723000
        inst.top_tracks_since.return_value = [
            {"mbid": "t1", "title": "Yellow", "artist": "Coldplay", "album": "P", "plays": 3},
        ]
        inst.top_artists_since.return_value = [
            {"artist": "Coldplay", "plays": 3, "distinct_tracks": 2},
        ]
        inst.recent_plays.return_value = [
            {"id": 9, "mbid": "t1", "played_at": "2026-04-27 10:00:00",
             "source": "desktop", "title": "Yellow", "artist": "Coldplay", "album": "P"},
        ]
        inst.plays_by_hour.return_value = [
            {"hour": 10, "plays": 3},
        ]
        res = client.get('/api/library/play-stats?since=2026-01-01&limit=5')

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["total"] == 7
    assert data["listening_time_ms"] == 3723000
    assert data["top_tracks"][0]["plays"] == 3
    assert data["top_artists"][0]["distinct_tracks"] == 2
    assert data["recent"][0]["mbid"] == "t1"
    assert data["hour_of_day"][10] == 3
    assert len(data["hour_of_day"]) == 24
    inst.play_count_since.assert_called_once_with("2026-01-01")
    inst.listening_time_since.assert_called_once_with("2026-01-01")
    inst.top_tracks_since.assert_called_once_with("2026-01-01", limit=5)
    inst.top_artists_since.assert_called_once_with("2026-01-01", limit=5)
    inst.recent_plays.assert_called_once_with(limit=5)
    inst.plays_by_hour.assert_called_once_with("2026-01-01")


def test_play_stats_defaults_to_no_since_and_limit_20(client, mock_config):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 0
        inst.listening_time_since.return_value = 0
        inst.top_tracks_since.return_value = []
        inst.top_artists_since.return_value = []
        inst.recent_plays.return_value = []
        inst.plays_by_hour.return_value = []
        res = client.get('/api/library/play-stats')

    assert res.status_code == 200
    inst.play_count_since.assert_called_once_with(None)
    inst.top_tracks_since.assert_called_once_with(None, limit=20)


def test_play_stats_clamps_limit_to_safe_range(client, mock_config):
    # Big limits would let a stale dashboard pull thousands of rows on
    # every poll; clamp so the worst-case payload is bounded.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 0
        inst.listening_time_since.return_value = 0
        inst.top_tracks_since.return_value = []
        inst.top_artists_since.return_value = []
        inst.recent_plays.return_value = []
        inst.plays_by_hour.return_value = []
        client.get('/api/library/play-stats?limit=99999')

    inst.top_tracks_since.assert_called_once_with(None, limit=200)


def test_play_stats_rejects_non_integer_limit(client, mock_config):
    res = client.get('/api/library/play-stats?limit=abc')
    assert res.status_code == 400
    assert "limit" in res.get_json()["message"]


# --- Stage 10a: Downloads endpoints --------------------------------------

@pytest.fixture
def _config_file_present(monkeypatch, tmp_path):
    """Make ``config_exists()`` return True so the setup-redirect
    ``before_request`` hook doesn't 302 to /setup. The rest of the
    suite assumes CONFIG_FILE is set externally; these tests are
    self-contained instead."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}")
    monkeypatch.setattr("web_server.CONFIG_FILE", str(cfg_path))


def test_downloads_list_returns_serialized_items(
    client,
    mock_config,
    _config_file_present,
    tmp_path,
):
    from datetime import datetime, timezone

    mock_config.downloads_dir = str(tmp_path)

    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        item = MagicMock()
        item.id = 7
        item.search_query = "Beck - Loser"
        item.status = "failed"
        item.last_attempt = None
        item.attempt_count = 2
        item.max_attempts = 3
        item.next_attempt_at = datetime(
            2026, 7, 22, 21, 15, tzinfo=timezone.utc
        )
        item.is_paused = False
        item.is_quarantined = True
        item.last_error = "Exact release remains incomplete"
        inst.get_all_downloads.return_value = [item]

        res = client.get('/api/downloads/list')

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["items"] == [
        {
            "id": 7,
            "query": "Beck - Loser",
            "status": "failed",
            "last_attempt": None,
            "attempt_count": 2,
            "max_attempts": 3,
            "next_attempt_at": "2026-07-22T21:15:00+00:00",
            "is_paused": False,
            "is_quarantined": True,
            "last_error": "Exact release remains incomplete",
            "retained_bytes": 0,
            "retained_directories": 0,
            "retained_files": 0,
            "retained_kinds": [],
        }
    ]
    assert body["residue"] == {
        "errors": [],
        "total_bytes": 0,
        "total_directories": 0,
        "total_files": 0,
        "unmatched_item_ids": [],
    }


def test_downloads_list_serializes_empty_retry_metadata_as_null(
    client,
    mock_config,
    _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        item = MagicMock(
            id=8,
            search_query="Portishead - Roads",
            status="pending",
            last_attempt=None,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=None,
            is_paused=False,
            is_quarantined=False,
            last_error=None,
        )
        inst.get_all_downloads.return_value = [item]

        res = client.get('/api/downloads/list')

    assert res.status_code == 200
    payload = res.get_json()["items"][0]
    assert payload["next_attempt_at"] is None
    assert payload["last_error"] is None


def test_retry_download_flips_failed_to_pending(client, mock_config, _config_file_present):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.retry_download.return_value = True

        res = client.post('/api/downloads/42/retry')

    assert res.status_code == 200
    assert res.get_json() == {"success": True}
    inst.retry_download.assert_called_once_with(42)


def test_delete_download_residue_requires_explicit_confirmation(
    client,
    mock_config,
    _config_file_present,
):
    res = client.delete('/api/downloads/42/residue', json={})

    assert res.status_code == 400
    assert "confirmation" in res.get_json()["message"]


def test_delete_download_residue_refuses_active_work(
    client,
    mock_config,
    _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        item = MagicMock(status="pending", claim_owner="runner")
        MockDB.return_value.__enter__.return_value.get_download.return_value = item

        res = client.delete(
            '/api/downloads/42/residue',
            json={"confirm": True},
        )

    assert res.status_code == 409
    assert "active" in res.get_json()["message"]


def test_delete_download_residue_removes_only_failed_item_evidence(
    client,
    mock_config,
    _config_file_present,
):
    removed = MagicMock(
        removed_bytes=1024,
        removed_directories=2,
        removed_files=10,
    )
    with patch('web_server.DatabaseManager') as MockDB, patch(
        'web_server.remove_download_residue',
        return_value=removed,
    ) as remove:
        item = MagicMock(status="failed", claim_owner=None)
        MockDB.return_value.__enter__.return_value.get_download.return_value = item

        res = client.delete(
            '/api/downloads/42/residue',
            json={"confirm": True},
        )

    assert res.status_code == 200
    assert res.get_json() == {
        "success": True,
        "removed_bytes": 1024,
        "removed_directories": 2,
        "removed_files": 10,
    }
    remove.assert_called_once_with(mock_config.downloads_dir, 42)


def test_retry_download_404s_when_row_not_failed(client, mock_config, _config_file_present):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.retry_download.return_value = False

        res = client.post('/api/downloads/42/retry')

    # 404 (not 200/success:false) so the desktop client can branch on
    # status code without parsing message strings.
    assert res.status_code == 404
    assert res.get_json()["success"] is False


def test_delete_download_removes_row(
    client,
    mock_config,
    _config_file_present,
    tmp_path,
):
    mock_config.downloads_dir = str(tmp_path)
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_download.return_value = MagicMock(claim_owner=None)
        res = client.delete('/api/downloads/7')

    assert res.status_code == 200
    assert res.get_json() == {"success": True}
    inst.remove_from_queue.assert_called_once_with(7)


def test_delete_download_refuses_to_orphan_retained_files(
    client,
    mock_config,
    _config_file_present,
    tmp_path,
):
    mock_config.downloads_dir = str(tmp_path)
    retained = tmp_path / ".dap-quarantine-7-evidence"
    retained.mkdir()
    (retained / "track.flac").write_bytes(b"evidence")
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_download.return_value = MagicMock(claim_owner=None)

        res = client.delete('/api/downloads/7')

    assert res.status_code == 409
    assert "retained files" in res.get_json()["message"]
    inst.remove_from_queue.assert_not_called()


def test_clear_completed_returns_removed_count(client, mock_config, _config_file_present):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.delete_succeeded_downloads.return_value = 3

        res = client.post('/api/downloads/clear-completed')

    assert res.status_code == 200
    assert res.get_json() == {"success": True, "removed": 3}


# --- Stage 10b: New Releases endpoint -----------------------------------

def test_releases_wanted_returns_disabled_when_lidarr_off(client, mock_config, _config_file_present):
    import web_server
    web_server.config._config = {"lidarr_watch_enabled": False}

    res = client.get('/api/releases/wanted')
    # 200 / success:false / reason — quiet empty-state on the client.
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False
    assert body["reason"] == "lidarr_disabled"


def test_releases_wanted_returns_disabled_when_lidarr_unavailable(client, mock_config, _config_file_present):
    import web_server
    web_server.config._config = {"lidarr_watch_enabled": True}
    with patch('src.downloader._build_lidarr_client', return_value=None):
        res = client.get('/api/releases/wanted')

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is False
    assert body["reason"] == "lidarr_unavailable"


def test_releases_wanted_502s_when_lidarr_errors(client, mock_config, _config_file_present):
    import web_server
    from src.lidarr_client import LidarrError
    web_server.config._config = {"lidarr_watch_enabled": True}

    fake_client = MagicMock()
    fake_client.get_wanted_missing.side_effect = LidarrError("connection refused")
    with patch('src.downloader._build_lidarr_client', return_value=fake_client):
        res = client.get('/api/releases/wanted')

    # 502 distinguishes "configured but offline" from "not configured"
    # so the UI can render the right copy on each.
    assert res.status_code == 502
    assert res.get_json()["success"] is False


def test_releases_wanted_augments_records_with_queue_and_library_state(
    client, mock_config, _config_file_present,
):
    import web_server
    web_server.config._config = {"lidarr_watch_enabled": True}

    fake_client = MagicMock()
    fake_client.get_wanted_missing.return_value = [
        {
            "foreignAlbumId": "rmb-wanted",
            "title": "Wanted",
            "artist": {"artistName": "A"},
            "releaseDate": "2026-01-01",
            "images": [{"coverType": "cover", "remoteUrl": "https://lidarr/cover.png"}],
        },
        {
            "foreignAlbumId": "rmb-queued",
            "title": "Queued",
            "artist": {"artistName": "B"},
            "releaseDate": "2026-02-01",
            "images": [],
        },
        {
            "foreignAlbumId": "rmb-have",
            "title": "Have",
            "artist": {"artistName": "C"},
            "releaseDate": "2026-03-01",
            "images": [],
        },
    ]

    with patch('src.downloader._build_lidarr_client', return_value=fake_client), \
            patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_sync_state.return_value = "2026-04-30T10:00:00"
        inst.get_queued_release_mbids.return_value = {"rmb-queued"}
        inst.get_existing_release_mbids.return_value = {"rmb-have"}
        res = client.get('/api/releases/wanted')

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["last_tick"] == "2026-04-30T10:00:00"
    assert len(body["items"]) == 3

    by_mbid = {it["mbid"]: it for it in body["items"]}
    # Lidarr-supplied cover preferred when present.
    assert by_mbid["rmb-wanted"]["cover_url"] == "https://lidarr/cover.png"
    # No images → fall back to coverartarchive.org by mbid.
    assert "coverartarchive.org" in by_mbid["rmb-queued"]["cover_url"]
    # Augmentation flags reflect the joins.
    assert by_mbid["rmb-wanted"] == {
        **by_mbid["rmb-wanted"],
        "queued": False, "downloaded": False,
    }
    assert by_mbid["rmb-queued"]["queued"] is True
    assert by_mbid["rmb-queued"]["downloaded"] is False
    assert by_mbid["rmb-have"]["queued"] is False
    assert by_mbid["rmb-have"]["downloaded"] is True


# --- Stage 11: Liked Songs endpoint --------------------------------------

def test_like_track_returns_liked_true(client, mock_config, _config_file_present):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.set_track_liked.return_value = True

        res = client.post('/api/library/tracks/abc/like')

    assert res.status_code == 200
    assert res.get_json() == {"success": True, "liked": True}
    inst.set_track_liked.assert_called_once_with("abc", True)
    # First like auto-creates the Liked Songs smart playlist.
    inst.ensure_liked_songs_playlist.assert_called_once()


def test_unlike_track_does_not_create_playlist(client, mock_config, _config_file_present):
    """DELETE unlikes but never auto-creates Liked Songs — the playlist
    only appears on the *first* like, so a user who unlikes immediately
    after a like doesn't keep getting it re-created."""
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.set_track_liked.return_value = False

        res = client.delete('/api/library/tracks/abc/like')

    assert res.status_code == 200
    assert res.get_json() == {"success": True, "liked": False}
    inst.set_track_liked.assert_called_once_with("abc", False)
    inst.ensure_liked_songs_playlist.assert_not_called()


def test_like_track_404s_when_unknown(client, mock_config, _config_file_present):
    # Matches the Stage 10a retry-download convention: status-code branch
    # without parsing the body so the desktop client can render an
    # inline error without throwing.
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.set_track_liked.return_value = None

        res = client.post('/api/library/tracks/missing/like')

    assert res.status_code == 404
    assert res.get_json()["success"] is False


def test_record_play_passes_listened_ms_to_db(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.record_play_event.return_value = 1

        res = client.post(
            '/api/library/plays',
            json={"mbid": "abc", "source": "desktop", "listened_ms": 45_000},
        )

    assert res.status_code == 201
    inst.record_play_event.assert_called_once_with(
        "abc", source="desktop", listened_ms=45_000,
    )


def test_record_play_caps_listened_ms_server_side(
    client, mock_config, _config_file_present,
):
    """30 minutes is the hard cap — a hostile client claiming a 6-hour
    listen for a 3-minute track shouldn't be able to inflate the
    listening-time stat."""
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.record_play_event.return_value = 1

        res = client.post(
            '/api/library/plays',
            json={"mbid": "abc", "listened_ms": 6 * 60 * 60 * 1000},
        )

    assert res.status_code == 201
    _, kwargs = inst.record_play_event.call_args
    assert kwargs["listened_ms"] == 30 * 60 * 1000


def test_record_play_rejects_negative_listened_ms(
    client, mock_config, _config_file_present,
):
    res = client.post(
        '/api/library/plays',
        json={"mbid": "abc", "listened_ms": -1},
    )
    assert res.status_code == 400


def test_record_play_rejects_non_numeric_listened_ms(
    client, mock_config, _config_file_present,
):
    # Strings (including numeric-looking ones) are rejected because
    # JSON has a real number type and accepting "45000" would silently
    # disagree with the int-coercion behavior the player relies on.
    res = client.post(
        '/api/library/plays',
        json={"mbid": "abc", "listened_ms": "45000"},
    )
    assert res.status_code == 400


def test_play_stats_includes_listening_time_ms(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 3
        inst.listening_time_since.return_value = 7_200_000  # 2 hours
        inst.top_tracks_since.return_value = []
        inst.top_artists_since.return_value = []
        inst.recent_plays.return_value = []
        inst.plays_by_hour.return_value = []

        res = client.get('/api/library/play-stats')

    assert res.status_code == 200
    body = res.get_json()
    assert body["listening_time_ms"] == 7_200_000


def test_play_stats_pads_hour_of_day_to_24_entries(
    client, mock_config, _config_file_present,
):
    """The DB helper only returns hours with plays; the endpoint pads
    so the heatmap renders fixed-width and missing hours read as 0."""
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 5
        inst.listening_time_since.return_value = 0
        inst.top_tracks_since.return_value = []
        inst.top_artists_since.return_value = []
        inst.recent_plays.return_value = []
        inst.plays_by_hour.return_value = [
            {"hour": 3, "plays": 2},
            {"hour": 22, "plays": 3},
        ]

        res = client.get('/api/library/play-stats')

    body = res.get_json()
    assert len(body["hour_of_day"]) == 24
    assert body["hour_of_day"][3] == 2
    assert body["hour_of_day"][22] == 3
    assert sum(body["hour_of_day"]) == 5
    # Out-of-range hours from a misbehaving DB row are dropped, not
    # crashed.
    assert body["hour_of_day"][0] == 0


def test_play_stats_drops_out_of_range_hours_silently(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.play_count_since.return_value = 0
        inst.listening_time_since.return_value = 0
        inst.top_tracks_since.return_value = []
        inst.top_artists_since.return_value = []
        inst.recent_plays.return_value = []
        inst.plays_by_hour.return_value = [
            # 25 isn't a real hour; the endpoint shouldn't IndexError.
            {"hour": 25, "plays": 99},
            {"hour": 12, "plays": 1},
        ]
        res = client.get('/api/library/play-stats')
    body = res.get_json()
    assert body["hour_of_day"][12] == 1
    assert sum(body["hour_of_day"]) == 1


def test_lyrics_returns_cached_row_without_calling_lrclib(
    client, mock_config, _config_file_present,
):
    """A manual-source row should never trigger an LRCLIB request, even
    if it's older than the TTL — the user's typed override wins."""
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.lrclib_client.fetch_lyrics') as fetch:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_lyrics.return_value = {
            "track_mbid": "m1",
            "lrc": "hand-written line\nanother",
            "synced": 0,
            "source": "manual",
            "fetched_at": "1999-01-01 00:00:00",
        }
        res = client.get('/api/library/tracks/m1/lyrics')

    assert res.status_code == 200
    body = res.get_json()
    assert body["source"] == "manual"
    assert body["lrc"].startswith("hand-written")
    fetch.assert_not_called()


def test_lyrics_fetches_lrclib_on_cache_miss_and_caches_result(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.lrclib_client.fetch_lyrics') as fetch:
        inst = MockDB.return_value.__enter__.return_value
        # First get_lyrics call (cache check) → no row. Final get_lyrics
        # (after upsert) returns the freshly cached row.
        inst.get_lyrics.side_effect = [
            None,
            {
                "track_mbid": "m1",
                "lrc": "[00:01.00] line",
                "synced": 1,
                "source": "lrclib",
                "fetched_at": "2026-05-13 10:00:00",
            },
        ]
        inst.get_live_track_identity.return_value = {
            "title": "Song", "artist": "Artist", "album": "Album",
        }

        fetch.return_value = {"lrc": "[00:01.00] line", "synced": True}

        res = client.get('/api/library/tracks/m1/lyrics')

    assert res.status_code == 200
    body = res.get_json()
    assert body["synced"] is True
    assert body["source"] == "lrclib"
    fetch.assert_called_once()
    # The cache must be populated with the LRCLIB result so the next
    # open doesn't re-hit the network.
    inst.upsert_lyrics.assert_called_once_with(
        "m1", "[00:01.00] line", True, "lrclib",
    )


def test_lyrics_caches_miss_so_repeated_opens_dont_refetch(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.lrclib_client.fetch_lyrics') as fetch:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_lyrics.return_value = None
        inst.get_live_track_identity.return_value = {
            "title": "Song", "artist": "Artist", "album": None,
        }
        fetch.return_value = None  # LRCLIB miss

        res = client.get('/api/library/tracks/m1/lyrics')

    assert res.status_code == 200
    body = res.get_json()
    assert body["lrc"] is None
    # Crucially: cache the miss as a negative-cache row.
    inst.upsert_lyrics.assert_called_once_with("m1", None, False, "lrclib")


def test_lyrics_post_clears_row_on_empty_lrc(
    client, mock_config, _config_file_present,
):
    """A blank manual paste should delete the row entirely so the
    next GET can re-try LRCLIB instead of being stuck on a blank
    manual override."""
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        res = client.post(
            '/api/library/tracks/m1/lyrics',
            json={"lrc": "   ", "synced": False},
        )
    assert res.status_code == 200
    # Clearing goes through the database facade, not an empty upsert.
    inst.upsert_lyrics.assert_not_called()
    inst.delete_lyrics.assert_called_once_with("m1")


def test_daily_mixes_regenerate_returns_summary_on_master(
    client, mock_config, _config_file_present,
):
    mock_config.is_master = True
    summary = {
        "mixes": 2,
        "reason": None,
        "details": [
            {"playlist_id": "daily_mix_1", "name": "Daily Mix 1: Rock",
             "tag": "rock", "artist_count": 5, "track_count": 40},
            {"playlist_id": "daily_mix_2", "name": "Daily Mix 2: Jazz",
             "tag": "jazz", "artist_count": 3, "track_count": 25},
        ],
    }
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.daily_mixes.regenerate_daily_mixes',
               return_value=summary) as regen:
        res = client.post('/api/library/daily-mixes/regenerate')
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["mixes"] == 2
    assert body["details"][0]["tag"] == "rock"
    regen.assert_called_once()


def test_daily_mixes_regenerate_refused_on_satellite(
    client, mock_config, _config_file_present,
):
    """Satellites get the rows via catalog sync — running the cluster
    locally would compete with the master's authoritative version."""
    mock_config.is_master = False
    res = client.post('/api/library/daily-mixes/regenerate')
    assert res.status_code == 400


def test_artist_radio_returns_serialized_playable_tracks(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.build_artist_radio.return_value = {
            "tracks": [
                {
                    "mbid": "m1", "title": "A", "artist": "X",
                    "album": "Al", "track_number": 1, "disc_number": 1,
                    "local_path": "/m/a", "dap_path": None,
                    "album_id": "Al|X", "is_liked": 0,
                },
                # Unavailable row — no local, no dap, no master configured.
                {
                    "mbid": "m2", "title": "B", "artist": "X",
                    "album": "Al", "track_number": 2, "disc_number": 1,
                    "local_path": None, "dap_path": None,
                    "album_id": "Al|X", "is_liked": 0,
                },
            ],
            "top_tag": "rock",
            "seed_count": 2,
            "related_count": 0,
        }
        res = client.get('/api/library/artists/X/radio?limit=10')

    assert res.status_code == 200
    body = res.get_json()
    assert body["top_tag"] == "rock"
    # Unavailable rows are stripped so next() can't land on a dead end.
    assert [t["mbid"] for t in body["tracks"]] == ["m1"]
    inst.build_artist_radio.assert_called_once_with("X", limit=10)


def test_artist_radio_rejects_non_integer_limit(
    client, mock_config, _config_file_present,
):
    res = client.get('/api/library/artists/X/radio?limit=many')
    assert res.status_code == 400


def test_tags_backfill_kicks_task_on_master(
    client, mock_config, _config_file_present,
):
    import web_server
    mock_config.is_master = True
    with patch.object(web_server.task_manager, "start_task",
                      return_value=(True, "ok")) as start:
        res = client.post('/api/library/tags/backfill', json={})
    assert res.status_code == 200
    args, _ = start.call_args
    # The task target is run_tag_backfill, given db_path + incremental.
    assert args[0] is web_server.run_tag_backfill
    assert args[1] == (mock_config.db_path, True)
    assert args[2] == "Genre tag backfill"


def test_tags_backfill_refuses_on_satellite(
    client, mock_config, _config_file_present,
):
    """Each satellite hammering MB independently would shred the
    rate-limit budget — the master runs the backfill once and the
    catalog-sync delta fans the rows out."""
    mock_config.is_master = False
    res = client.post('/api/library/tags/backfill', json={})
    assert res.status_code == 400


def test_like_track_on_satellite_proxies_to_master_and_mirrors_locally(
    client, mock_config, _config_file_present,
):
    """When master_url is set the satellite must forward to the master
    so the like survives the next catalog pull; the local DB is
    mirrored on success so the UI doesn't bounce on page reload."""
    import web_server
    web_server.config._config = {
        "master_url": "http://master.local:5001",
        "api_token": "tok",
    }

    upstream = MagicMock()
    upstream.status_code = 200
    upstream.json.return_value = {"success": True, "liked": True}

    with patch('web_server.DatabaseManager') as MockDB, \
         patch('requests.request', return_value=upstream) as proxy:
        inst = MockDB.return_value.__enter__.return_value
        # When api_token is set on this satellite, the before-request
        # auth gate requires a Bearer header on /api/* — supply it so
        # the test exercises the proxy path, not the 401 path.
        res = client.post(
            '/api/library/tracks/m1/like',
            headers={"Authorization": "Bearer tok"},
        )

    assert res.status_code == 200
    assert res.get_json() == {"success": True, "liked": True}
    # Forwarded to the master with the bearer token.
    args, kwargs = proxy.call_args
    assert args[0] == "POST"
    assert args[1] == "http://master.local:5001/api/library/tracks/m1/like"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    # And the local DB is mirrored so the catalog pull's eventual
    # reconciliation doesn't visibly snap-back the heart.
    inst.set_track_liked.assert_called_once_with("m1", True)
    inst.ensure_liked_songs_playlist.assert_called_once()


def test_like_track_returns_502_when_master_unreachable(
    client, mock_config, _config_file_present,
):
    import web_server
    web_server.config._config = {"master_url": "http://master.local:5001"}

    with patch('requests.request') as proxy:
        import requests as _req
        proxy.side_effect = _req.ConnectionError("nope")
        res = client.post('/api/library/tracks/m1/like')

    assert res.status_code == 502
    assert res.get_json()["success"] is False


def test_delete_liked_songs_playlist_is_refused_with_409(
    client, mock_config, _config_file_present,
):
    """The reserved Liked Songs id would auto-recreate on next like —
    refuse the delete so the user can't end up in a flicker loop."""
    res = client.delete('/api/playlists/liked_songs')
    assert res.status_code == 409
    body = res.get_json()
    assert body["success"] is False
    assert "system playlist" in body["message"].lower()


def test_lyrics_404s_when_track_missing_from_library(
    client, mock_config, _config_file_present,
):
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.get_lyrics.return_value = None
        inst.get_live_track_identity.return_value = None

        res = client.get('/api/library/tracks/missing/lyrics')
    assert res.status_code == 404


def test_wrapped_defaults_year_to_current_utc(
    client, mock_config, _config_file_present,
):
    from datetime import datetime, timezone
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.wrapped_summary.return_value = {
            "year": datetime.now(timezone.utc).year,
            "total_plays": 0, "total_listening_time_ms": 0,
            "has_legacy_rows": False, "top_track": None, "top_artist": None,
            "top_album": None, "busiest_day": None, "top_hour": None,
            "first_play": None, "longest_streak_days": 0,
        }

        res = client.get('/api/library/wrapped')

    assert res.status_code == 200
    inst.wrapped_summary.assert_called_once_with(datetime.now(timezone.utc).year)


def test_wrapped_rejects_non_integer_year(
    client, mock_config, _config_file_present,
):
    res = client.get('/api/library/wrapped?year=last')
    assert res.status_code == 400


def test_wrapped_propagates_db_value_error_as_400(
    client, mock_config, _config_file_present,
):
    """The DB helper raises ValueError on an unreasonable year (e.g.
    year=99) — the endpoint should surface that as a 400, not a 500."""
    with patch('web_server.DatabaseManager') as MockDB:
        inst = MockDB.return_value.__enter__.return_value
        inst.wrapped_summary.side_effect = ValueError("unreasonable year: 99")
        res = client.get('/api/library/wrapped?year=99')
    assert res.status_code == 400


def test_home_bundles_cards_in_one_response(
    client, mock_config, _config_file_present,
):
    """The Home screen does multiple conceptual fetches in one round trip —
    confirm the endpoint actually returns each section under its keyed
    name so the frontend's optional-chaining defaults don't silently
    swallow a regression."""
    with patch('web_server.DatabaseManager') as MockDB, \
         patch('src.daily_mixes.list_daily_mixes', return_value=[]):
        inst = MockDB.return_value.__enter__.return_value
        inst.recent_plays.return_value = [
            {
                "id": 1, "mbid": "m1", "played_at": "2026-05-13 10:00:00",
                "source": "desktop", "title": "A", "artist": "X",
                "album": "Al", "album_id": "rmb-1",
            }
        ]
        inst.top_artists_since.return_value = [
            {"artist": "X", "plays": 5, "distinct_tracks": 3},
        ]
        inst.get_liked_tracks_summary.return_value = {
            "total": 4,
            "preview": [
                {"mbid": "m1", "title": "A", "artist": "X", "album": "Al",
                 "album_id": "rmb-1"},
            ],
        }

        res = client.get('/api/library/home')

    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert len(body["recent"]) == 1
    assert len(body["top_artists"]) == 1
    assert body["liked"]["total"] == 4
    assert len(body["liked"]["preview"]) == 1
    # Jump-back-in derived from recent — one unique album_id here.
    assert body["jump_back_in"] == [
        {"album_id": "rmb-1", "title": "Al", "artist": "X"}
    ]
    assert body["daily_mixes"] == []
