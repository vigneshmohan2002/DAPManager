from inspect import signature
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.catalog_sync import (
    ARTIST_TAGS_SYNC_STATE_KEY,
    CatalogClient,
    LYRICS_SYNC_STATE_KEY,
    PLAYLIST_PUSH_STATE_KEY,
    PLAYLIST_SYNC_STATE_KEY,
    SYNC_STATE_KEY,
    main_run_catalog_pull,
    main_run_artist_tags_pull,
    main_run_lyrics_pull,
    main_run_playlist_pull,
    main_run_playlist_push,
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


def test_catalog_client_public_constructor_contract():
    params = signature(CatalogClient).parameters
    assert tuple(params) == (
        "db",
        "master_url",
        "progress_callback",
        "timeout",
        "api_token",
    )
    assert params["progress_callback"].default is None
    assert params["timeout"].default == 30
    assert params["api_token"].default is None


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


def test_pull_does_not_advance_cursor_when_applying_a_row_fails(db):
    old_cursor = "2026-04-18 12:00:00"
    db.set_sync_state(SYNC_STATE_KEY, old_cursor)
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-04-18 13:00:00",
        "tracks": [{"mbid": "m1", "title": "Song", "artist": "Artist"}],
    }
    with patch.object(
        client.session, "get", return_value=_mock_response(payload)
    ), patch.object(
        db, "apply_catalog_row", side_effect=RuntimeError("write failed")
    ):
        with pytest.raises(RuntimeError, match="write failed"):
            client.pull()

    assert db.get_sync_state(SYNC_STATE_KEY) == old_cursor


def test_pull_does_not_advance_cursor_when_as_of_missing(db):
    db.set_sync_state(SYNC_STATE_KEY, "2026-04-18 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": True, "count": 0, "tracks": []}  # no as_of
    with patch.object(client.session, "get", return_value=_mock_response(payload)):
        client.pull()
    # Unchanged
    assert db.get_sync_state(SYNC_STATE_KEY) == "2026-04-18 12:00:00"


@pytest.mark.parametrize(
    (
        "pull_method",
        "apply_method",
        "state_key",
        "endpoint",
        "collection_key",
        "batch_size",
        "fetch_label",
        "apply_label",
        "completion_label",
        "includes_stale",
    ),
    [
        (
            "pull",
            "apply_catalog_row",
            SYNC_STATE_KEY,
            "/api/catalog",
            "tracks",
            100,
            "catalog",
            "catalog rows",
            "Catalog",
            False,
        ),
        (
            "pull_playlists",
            "apply_playlist_row",
            PLAYLIST_SYNC_STATE_KEY,
            "/api/playlists",
            "playlists",
            25,
            "playlist",
            "playlists",
            "Playlist",
            False,
        ),
        (
            "pull_lyrics",
            "apply_lyrics_row",
            LYRICS_SYNC_STATE_KEY,
            "/api/lyrics",
            "lyrics",
            100,
            "lyrics",
            "lyrics",
            "Lyrics",
            True,
        ),
        (
            "pull_artist_tags",
            "apply_artist_tags_row",
            ARTIST_TAGS_SYNC_STATE_KEY,
            "/api/artist-tags",
            "artist_tags",
            100,
            "artist-tag",
            "artist tags",
            "Artist-tag",
            True,
        ),
    ],
)
def test_delta_pull_protocol_and_progress_contract(
    db,
    pull_method,
    apply_method,
    state_key,
    endpoint,
    collection_key,
    batch_size,
    fetch_label,
    apply_label,
    completion_label,
    includes_stale,
):
    since = "2026-06-01 10:00:00"
    as_of = "2026-06-01 11:00:00"
    db.set_sync_state(state_key, since)
    rows = [{"row": index} for index in range(batch_size + 1)]
    actions = ["inserted", "updated", "stale"] + [
        "skipped"
    ] * (batch_size - 2)
    payload = {
        "success": True,
        "as_of": as_of,
        collection_key: rows,
    }
    progress = []
    client = CatalogClient(
        db=db,
        master_url="http://host.local:5001",
        progress_callback=progress.append,
    )

    with patch.object(
        db,
        apply_method,
        side_effect=actions,
    ) as apply_row, patch.object(
        client.session,
        "get",
        return_value=_mock_response(payload),
    ) as mock_get:
        summary = getattr(client, pull_method)()

    assert mock_get.call_args.args[0] == f"http://host.local:5001{endpoint}"
    assert mock_get.call_args.kwargs["params"] == {"since": since}
    assert apply_row.call_count == batch_size + 1
    assert db.get_sync_state(state_key) == as_of
    assert progress[0] == {"message": f"Fetching {fetch_label} delta since {since}"}
    assert progress[-3:-1] == [
        {
            "message": f"Applying {apply_label} ({batch_size}/{batch_size + 1})",
            "current": batch_size,
            "total": batch_size + 1,
        },
        {
            "message": f"Applying {apply_label} ({batch_size + 1}/{batch_size + 1})",
            "current": batch_size + 1,
            "total": batch_size + 1,
        },
    ]

    expected_skipped = batch_size - 2 if includes_stale else batch_size - 1
    expected = {
        "received": batch_size + 1,
        "inserted": 1,
        "updated": 1,
        "skipped": expected_skipped,
        "since": since,
        "as_of": as_of,
    }
    completion_parts = ["1 new", "1 updated"]
    if includes_stale:
        expected["stale"] = 1
        completion_parts.append("1 stale")
    completion_parts.append(f"{expected_skipped} skipped")
    assert summary == expected
    assert progress[-1] == {
        "message": f"{completion_label} pull done: {', '.join(completion_parts)}"
    }


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


# ---------------------------------------------------------------------------
# Playlist push
# ---------------------------------------------------------------------------

def test_push_playlists_sends_only_rows_after_cursor(db):
    import time

    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_playlist(Playlist(playlist_id="old", name="Old", spotify_url=""))
    db.link_track_to_playlist("old", "t1", 0)

    cutoff = db.conn.execute("SELECT CURRENT_TIMESTAMP AS t").fetchone()["t"]
    db.set_sync_state(PLAYLIST_PUSH_STATE_KEY, cutoff)
    time.sleep(1.1)

    db.add_or_update_playlist(Playlist(playlist_id="new", name="New", spotify_url=""))
    db.link_track_to_playlist("new", "t1", 0)

    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True, "received": 1, "accepted": 1, "stale": 0, "skipped": 0,
        "results": [{"playlist_id": "new", "result": "inserted"}],
    }
    with patch.object(client.session, "post", return_value=_mock_response(payload)) as mock_post:
        summary = client.push_playlists()

    sent = mock_post.call_args.kwargs["json"]["playlists"]
    assert [p["playlist_id"] for p in sent] == ["new"]
    assert summary["sent"] == 1
    assert summary["accepted"] == 1
    # Cursor advances
    assert db.get_sync_state(PLAYLIST_PUSH_STATE_KEY) != cutoff


def test_push_playlists_empty_advances_cursor_without_post(db):
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    with patch.object(client.session, "post") as mock_post:
        summary = client.push_playlists()

    mock_post.assert_not_called()
    assert summary["sent"] == 0
    assert db.get_sync_state(PLAYLIST_PUSH_STATE_KEY) is not None


def test_push_playlists_raises_on_master_failure_keeps_cursor(db):
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="A", spotify_url=""))

    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {"success": False, "message": "bad data"}
    with patch.object(client.session, "post", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="bad data"):
            client.push_playlists()
    assert db.get_sync_state(PLAYLIST_PUSH_STATE_KEY) is None


def test_push_playlists_sends_full_playlist_with_tracks(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_track(Track(mbid="t2", title="T2", artist="B"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="Mix", spotify_url=""))
    db.link_track_to_playlist("p1", "t1", 0)
    db.link_track_to_playlist("p1", "t2", 1)

    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True, "received": 1, "accepted": 1, "stale": 0, "skipped": 0,
        "results": [],
    }
    with patch.object(client.session, "post", return_value=_mock_response(payload)) as mock_post:
        client.push_playlists()

    sent = mock_post.call_args.kwargs["json"]["playlists"]
    assert len(sent) == 1
    assert sent[0]["playlist_id"] == "p1"
    assert [t["track_mbid"] for t in sent[0]["tracks"]] == ["t1", "t2"]


def test_main_run_playlist_push_uses_config_master_url(db):
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="A", spotify_url=""))
    config = {"master_url": "http://host.local:5001/"}
    payload = {"success": True, "received": 1, "accepted": 1, "stale": 0, "skipped": 0, "results": []}
    with patch("src.catalog_sync.requests.Session.post",
               return_value=_mock_response(payload)) as mock_post:
        main_run_playlist_push(db, config)
    assert mock_post.call_args.args[0] == "http://host.local:5001/api/playlists"


def test_pull_lyrics_applies_rows_and_advances_cursor(db):
    """Stage 13 follow-up sanity check — lyrics sync fans out cached
    LRCLIB hits + manual overrides + negative-cache misses across the
    fleet using the same cursor pattern as catalog/playlists."""
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-05-13 12:00:00",
        "count": 3,
        "lyrics": [
            {
                "track_mbid": "m1", "lrc": "[00:01.00] hi",
                "synced": True, "source": "lrclib",
                "fetched_at": "2026-05-13 11:00:00",
            },
            {
                "track_mbid": "m2", "lrc": "hand-typed",
                "synced": False, "source": "manual",
                "fetched_at": "2026-05-13 11:30:00",
            },
            # Negative cache row — must propagate so satellites don't
            # re-hit LRCLIB for known-empty tracks.
            {
                "track_mbid": "m3", "lrc": None,
                "synced": False, "source": "lrclib",
                "fetched_at": "2026-05-13 11:45:00",
            },
        ],
    }
    with patch.object(
        client.session, "get", return_value=_mock_response(payload)
    ) as mock_get:
        summary = client.pull_lyrics()

    assert mock_get.call_args.args[0] == "http://host.local:5001/api/lyrics"
    assert mock_get.call_args.kwargs["params"] == {}
    assert summary == {
        "received": 3, "inserted": 3, "updated": 0,
        "stale": 0, "skipped": 0, "since": None,
        "as_of": "2026-05-13 12:00:00",
    }
    assert db.get_sync_state(LYRICS_SYNC_STATE_KEY) == "2026-05-13 12:00:00"
    assert db.get_lyrics("m3")["lrc"] is None


def test_main_run_lyrics_pull_uses_config_master_url(db):
    config = {"master_url": "http://host.local:5001/"}
    payload = {
        "success": True, "as_of": "2026-05-13 12:00:00",
        "count": 0, "lyrics": [],
    }
    with patch(
        "src.catalog_sync.requests.Session.get",
        return_value=_mock_response(payload),
    ) as mock_get:
        main_run_lyrics_pull(db, config)
    assert mock_get.call_args.args[0] == "http://host.local:5001/api/lyrics"


def test_pull_artist_tags_replaces_snapshots_and_advances_cursor(db):
    db.apply_artist_tags_row({
        "artist_name": "Changed",
        "mbid": "old-mbid",
        "fetched_at": "2026-05-01 10:00:00",
        "tags": [{"tag": "old", "weight": 2}],
    })
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-06-03 12:00:00",
        "count": 2,
        "artist_tags": [
            {
                "artist_name": "Changed",
                "mbid": "new-mbid",
                "fetched_at": "2026-06-03 11:00:00",
                "tags": [{"tag": "new", "weight": 9}],
            },
            {
                "artist_name": "No Match",
                "mbid": None,
                "fetched_at": "2026-06-03 11:30:00",
                "tags": [],
            },
        ],
    }
    with patch.object(
        client.session, "get", return_value=_mock_response(payload)
    ) as mock_get:
        summary = client.pull_artist_tags()

    assert mock_get.call_args.args[0] == "http://host.local:5001/api/artist-tags"
    assert mock_get.call_args.kwargs["params"] == {}
    assert summary == {
        "received": 2,
        "inserted": 1,
        "updated": 1,
        "stale": 0,
        "skipped": 0,
        "since": None,
        "as_of": "2026-06-03 12:00:00",
    }
    assert db.get_top_tags_for_artist("Changed") == [
        {"tag": "new", "weight": 9},
    ]
    assert db.get_top_tags_for_artist("No Match") == []
    assert db.get_sync_state(ARTIST_TAGS_SYNC_STATE_KEY) == (
        "2026-06-03 12:00:00"
    )


def test_pull_artist_tags_uses_its_own_cursor(db):
    db.set_sync_state(ARTIST_TAGS_SYNC_STATE_KEY, "2026-06-03 12:00:00")
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    payload = {
        "success": True,
        "as_of": "2026-06-04 12:00:00",
        "count": 0,
        "artist_tags": [],
    }
    with patch.object(
        client.session, "get", return_value=_mock_response(payload)
    ) as mock_get:
        client.pull_artist_tags()

    assert mock_get.call_args.kwargs["params"] == {
        "since": "2026-06-03 12:00:00",
    }
    assert db.get_sync_state(ARTIST_TAGS_SYNC_STATE_KEY) == (
        "2026-06-04 12:00:00"
    )


def test_main_run_artist_tags_pull_uses_config_master_url(db):
    config = {"master_url": "http://host.local:5001/"}
    payload = {
        "success": True,
        "as_of": "2026-06-03 12:00:00",
        "count": 0,
        "artist_tags": [],
    }
    with patch(
        "src.catalog_sync.requests.Session.get",
        return_value=_mock_response(payload),
    ) as mock_get:
        main_run_artist_tags_pull(db, config)
    assert mock_get.call_args.args[0] == (
        "http://host.local:5001/api/artist-tags"
    )


def test_catalog_client_sets_bearer_header_when_token_given(db):
    client = CatalogClient(
        db=db, master_url="http://host.local:5001", api_token="t0k3n"
    )
    assert client.session.headers.get("Authorization") == "Bearer t0k3n"


def test_catalog_client_no_auth_header_without_token(db):
    client = CatalogClient(db=db, master_url="http://host.local:5001")
    assert "Authorization" not in client.session.headers
