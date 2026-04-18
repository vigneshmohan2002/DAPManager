from unittest.mock import MagicMock, patch

import pytest
import requests

from src.catalog_sync import (
    CatalogClient,
    PLAYLIST_SYNC_STATE_KEY,
    SYNC_STATE_KEY,
    main_run_catalog_pull,
    main_run_playlist_pull,
)
from src.db_manager import DatabaseManager, Playlist, Track


@pytest.fixture
def db():
    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


def _mock_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def test_pull_initial_sends_no_since_and_applies_rows(db):
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-04-18 12:00:00",
        "count": 2,
        "tracks": [
            {"mbid": "m1", "title": "Song 1", "artist": "A", "updated_at": "2026-04-18 11:00:00"},
            {"mbid": "m2", "title": "Song 2", "artist": "B", "updated_at": "2026-04-18 11:30:00"},
        ],
    }
    with patch.object(client.session, "get", return_value=_mock_response(payload)) as mock_get:
        summary = client.pull()

    # No ?since on first call
    call = mock_get.call_args
    assert call.args[0] == "http://host.local:5001/api/catalog"
    assert call.kwargs["params"] == {}

    assert summary == {
        "received": 2,
        "inserted": 2,
        "updated": 0,
        "skipped": 0,
        "since": None,
        "as_of": "2026-04-18 12:00:00",
    }
    assert db.get_track_by_mbid("m1").title == "Song 1"
    assert db.get_sync_state(SYNC_STATE_KEY) == "2026-04-18 12:00:00"


def test_pull_uses_stored_cursor_on_subsequent_call(db):
    db.set_sync_state(SYNC_STATE_KEY, "2026-04-18 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": True, "as_of": "2026-04-18 13:00:00", "count": 0, "tracks": []}
    with patch.object(client.session, "get", return_value=_mock_response(payload)) as mock_get:
        summary = client.pull()

    assert mock_get.call_args.kwargs["params"] == {"since": "2026-04-18 12:00:00"}
    assert summary["since"] == "2026-04-18 12:00:00"
    assert summary["as_of"] == "2026-04-18 13:00:00"
    assert db.get_sync_state(SYNC_STATE_KEY) == "2026-04-18 13:00:00"


def test_pull_preserves_local_path_on_updated_row(db):
    db.add_or_update_track(Track(
        mbid="m1",
        title="Stale",
        artist="Stale",
        local_path="/music/m1.flac",
    ))
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-04-18 12:00:00",
        "count": 1,
        "tracks": [{"mbid": "m1", "title": "Fresh", "artist": "Fresh",
                    "updated_at": "2026-04-18 11:00:00"}],
    }
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        summary = client.pull()

    assert summary["updated"] == 1
    assert summary["inserted"] == 0
    track = db.get_track_by_mbid("m1")
    assert track.title == "Fresh"
    assert track.local_path == "/music/m1.flac"


def test_pull_raises_on_master_failure(db):
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": False, "message": "db locked"}
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="db locked"):
            client.pull()
    # Cursor NOT advanced on failure
    assert db.get_sync_state(SYNC_STATE_KEY) is None


def test_pull_does_not_advance_cursor_when_as_of_missing(db):
    db.set_sync_state(SYNC_STATE_KEY, "2026-04-18 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": True, "count": 0, "tracks": []}  # no as_of
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        client.pull()
    # Unchanged
    assert db.get_sync_state(SYNC_STATE_KEY) == "2026-04-18 12:00:00"


def test_pull_skips_rows_without_mbid(db):
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-04-18 12:00:00",
        "count": 2,
        "tracks": [
            {"title": "No MBID", "artist": "X"},
            {"mbid": "m1", "title": "Ok", "artist": "Y"},
        ],
    }
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        summary = client.pull()

    assert summary["inserted"] == 1
    assert summary["skipped"] == 1


def test_main_run_catalog_pull_reads_master_url_from_config(db):
    config = {"master_url": "http://host.local:5001/"}
    payload = {"success": True, "as_of": "2026-04-18 12:00:00", "count": 0, "tracks": []}
    with patch("src.catalog_sync.requests.Session.get",
               return_value=_mock_response(payload)) as mock_get:
        summary = main_run_catalog_pull(db, config)

    # Trailing slash stripped
    assert mock_get.call_args.args[0] == "http://host.local:5001/api/catalog"
    assert summary["received"] == 0


def test_catalog_client_requires_master_url(db):
    with pytest.raises(ValueError):
        CatalogClient(db=db, master_url="")


# ---------------------------------------------------------------------------
# Playlist pull
# ---------------------------------------------------------------------------

def test_pull_playlists_applies_rows_and_advances_cursor(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-04-18 12:00:00",
        "count": 1,
        "playlists": [{
            "playlist_id": "p1",
            "name": "Remote",
            "spotify_url": "",
            "updated_at": "2026-04-18 11:00:00",
            "tracks": [{"track_mbid": "t1", "track_order": 0}],
        }],
    }
    with patch.object(client.session, "get", return_value=_mock_response(payload)) as mock_get:
        summary = client.pull_playlists()

    assert mock_get.call_args.args[0] == "http://host.local:5001/api/playlists"
    assert mock_get.call_args.kwargs["params"] == {}
    assert summary["inserted"] == 1
    assert summary["as_of"] == "2026-04-18 12:00:00"
    assert db.get_sync_state(PLAYLIST_SYNC_STATE_KEY) == "2026-04-18 12:00:00"
    assert [t.mbid for t in db.get_playlist_tracks("p1")] == ["t1"]


def test_pull_playlists_uses_stored_cursor(db):
    db.set_sync_state(PLAYLIST_SYNC_STATE_KEY, "2026-04-18 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": True, "as_of": "2026-04-18 13:00:00", "count": 0, "playlists": []}
    with patch.object(client.session, "get", return_value=_mock_response(payload)) as mock_get:
        client.pull_playlists()

    assert mock_get.call_args.kwargs["params"] == {"since": "2026-04-18 12:00:00"}
    assert db.get_sync_state(PLAYLIST_SYNC_STATE_KEY) == "2026-04-18 13:00:00"


def test_pull_playlists_raises_on_failure_and_keeps_cursor(db):
    db.set_sync_state(PLAYLIST_SYNC_STATE_KEY, "2026-04-18 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": False, "message": "boom"}
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="boom"):
            client.pull_playlists()
    assert db.get_sync_state(PLAYLIST_SYNC_STATE_KEY) == "2026-04-18 12:00:00"


def test_main_run_playlist_pull_uses_config_master_url(db):
    config = {"master_url": "http://host.local:5001/"}
    payload = {"success": True, "as_of": "2026-04-18 12:00:00", "count": 0, "playlists": []}
    with patch("src.catalog_sync.requests.Session.get",
               return_value=_mock_response(payload)) as mock_get:
        main_run_playlist_pull(db, config)
    assert mock_get.call_args.args[0] == "http://host.local:5001/api/playlists"
