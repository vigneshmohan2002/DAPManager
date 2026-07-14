from datetime import datetime, timezone
from unittest.mock import MagicMock, call

from src.services.library_application_service import (
    apply_track_like,
    build_home_payload,
)
from src.services.listening_service import (
    ListeningServiceResult,
    PlayEventRequest,
    build_play_stats,
    prepare_play_event,
)
from src.services.lyrics_service import (
    load_track_lyrics,
    save_manual_lyrics,
)


def _database_factory(db):
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    return factory


def test_apply_track_like_keeps_local_mutation_order():
    db = MagicMock()
    db.set_track_liked.return_value = True
    factory = _database_factory(db)
    sender = MagicMock()

    result = apply_track_like(
        db_path="library.db",
        database_factory=factory,
        config_data={},
        mbid="track-1",
        method="POST",
        request_sender=sender,
    )

    assert result.payload == {"success": True, "liked": True}
    assert result.status_code == 200
    sender.assert_not_called()
    assert db.method_calls == [
        call.set_track_liked("track-1", True),
        call.ensure_liked_songs_playlist(),
    ]


def test_apply_track_like_proxies_then_mirrors_with_auth():
    db = MagicMock()
    factory = _database_factory(db)
    upstream = MagicMock(status_code=200)
    sender = MagicMock(return_value=upstream)

    result = apply_track_like(
        db_path="satellite.db",
        database_factory=factory,
        config_data={
            "master_url": "http://master.test:5001/",
            "api_token": " secret ",
        },
        mbid="track-1",
        method="DELETE",
        request_sender=sender,
    )

    assert result.payload == {"success": True, "liked": False}
    sender.assert_called_once_with(
        "DELETE",
        "http://master.test:5001/api/library/tracks/track-1/like",
        headers={"Authorization": "Bearer secret"},
        timeout=(5, 10),
    )
    db.set_track_liked.assert_called_once_with("track-1", False)
    db.ensure_liked_songs_playlist.assert_not_called()


def test_prepare_play_event_preserves_validation_order_and_cap():
    missing = prepare_play_event(
        {"mbid": " ", "source": 123, "listened_ms": -1}
    )
    assert isinstance(missing, ListeningServiceResult)
    assert missing.payload["message"] == "mbid is required"

    invalid_source = prepare_play_event(
        {"mbid": "track-1", "source": 123, "listened_ms": -1}
    )
    assert isinstance(invalid_source, ListeningServiceResult)
    assert invalid_source.payload["message"] == (
        "source must be a string when provided"
    )

    prepared = prepare_play_event(
        {"mbid": " track-1 ", "listened_ms": 6 * 60 * 60 * 1000}
    )
    assert prepared == PlayEventRequest(
        mbid="track-1",
        source=None,
        listened_ms=30 * 60 * 1000,
    )


def test_build_play_stats_keeps_query_order_and_hour_padding():
    db = MagicMock()
    db.play_count_since.return_value = 3
    db.listening_time_since.return_value = 45_000
    db.top_tracks_since.return_value = [{"mbid": "track-1"}]
    db.top_artists_since.return_value = [{"artist": "Artist"}]
    db.recent_plays.return_value = [{"id": 1}]
    db.plays_by_hour.return_value = [
        {"hour": 4, "plays": 2},
        {"hour": 25, "plays": 99},
    ]

    payload = build_play_stats(db, since="2026-01-01", limit=5)

    assert payload["total"] == 3
    assert payload["listening_time_ms"] == 45_000
    assert len(payload["hour_of_day"]) == 24
    assert payload["hour_of_day"][4] == 2
    assert sum(payload["hour_of_day"]) == 2
    assert db.method_calls == [
        call.play_count_since("2026-01-01"),
        call.listening_time_since("2026-01-01"),
        call.top_tracks_since("2026-01-01", limit=5),
        call.top_artists_since("2026-01-01", limit=5),
        call.recent_plays(limit=5),
        call.plays_by_hour("2026-01-01"),
    ]


def test_save_manual_lyrics_uses_facade_delete_for_blank_override():
    db = MagicMock()

    result = save_manual_lyrics(
        db,
        mbid="track-1",
        lrc="   ",
        synced=False,
    )

    assert result.payload == {"success": True, "lrc": None}
    db.delete_lyrics.assert_called_once_with("track-1")
    db.upsert_lyrics.assert_not_called()


def test_load_track_lyrics_serves_manual_cache_without_fetching():
    db = MagicMock()
    db.get_lyrics.return_value = {
        "lrc": "manual line",
        "synced": 0,
        "source": "manual",
        "fetched_at": "1999-01-01 00:00:00",
    }
    fetch = MagicMock()

    result = load_track_lyrics(db, mbid="track-1", fetch_lyrics=fetch)

    assert result.payload["source"] == "manual"
    assert result.payload["lrc"] == "manual line"
    fetch.assert_not_called()
    db.get_live_track_identity.assert_not_called()


def test_load_track_lyrics_caches_lrclib_miss_through_facade():
    db = MagicMock()
    db.get_lyrics.return_value = None
    db.get_live_track_identity.return_value = {
        "title": "Track",
        "artist": "Artist",
        "album": None,
    }
    fetch = MagicMock(return_value=None)

    result = load_track_lyrics(db, mbid="track-1", fetch_lyrics=fetch)

    assert result.payload == {
        "success": True,
        "lrc": None,
        "synced": False,
        "source": "lrclib",
        "fetched_at": None,
    }
    fetch.assert_called_once_with(
        track_name="Track",
        artist_name="Artist",
        album_name=None,
    )
    db.upsert_lyrics.assert_called_once_with(
        "track-1", None, False, "lrclib"
    )


def test_build_home_payload_preserves_rollups_and_unique_album_order():
    db = MagicMock()
    db.recent_plays.return_value = [
        {"album_id": "a1", "album": "One", "artist": "Artist 1"},
        {"album_id": "a1", "album": "One", "artist": "Artist 1"},
        {"album_id": "a2", "album": "Two", "artist": None},
        {"album_id": None, "album": "No link", "artist": "Artist 3"},
    ]
    db.top_artists_since.return_value = [{"artist": "Artist 1"}]
    db.get_liked_tracks_summary.return_value = {
        "total": 4,
        "preview": [{"mbid": "track-1"}],
    }
    load_mixes = MagicMock(return_value=[{"playlist_id": "mix-1"}])

    payload = build_home_payload(
        db,
        load_mixes,
        now=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert payload["liked"] == {
        "total": 4,
        "preview": [{"mbid": "track-1"}],
    }
    assert payload["jump_back_in"] == [
        {"album_id": "a1", "title": "One", "artist": "Artist 1"},
        {"album_id": "a2", "title": "Two", "artist": ""},
    ]
    db.recent_plays.assert_called_once_with(limit=12)
    db.top_artists_since.assert_called_once_with(
        "2026-06-15 12:00:00", limit=8
    )
    db.get_liked_tracks_summary.assert_called_once_with(limit=6)
    load_mixes.assert_called_once_with(db)
