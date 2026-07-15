from unittest.mock import MagicMock, call

import pytest

from src.services.library_application_service import (
    LibraryApplicationResult,
    delete_orphan_track_file,
)
from src.services.playlist_service import (
    PlaylistServiceResult,
    apply_pushed_playlists,
)


class TrackingContext:
    def __init__(self, store, events):
        self.store = store
        self.events = events

    def __enter__(self):
        self.events.append("db-enter")
        return self.store

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("db-exit")


class OrphanStore:
    def __init__(self, rows, events):
        self.rows = rows
        self.events = events

    def get_orphan_tracks(self):
        self.events.append("get-orphans")
        return self.rows

    def update_track_local_path(self, mbid, path):
        self.events.append(("clear-path", mbid, path))


def orphan_factory(store, events):
    def factory(db_path):
        events.append(("db-factory", db_path))
        return TrackingContext(store, events)

    return factory


def test_apply_pushed_playlists_preserves_order_and_lww_counts():
    db = MagicMock()
    db.apply_pushed_playlist_row.side_effect = [
        "inserted",
        "updated",
        "stale",
        "skipped",
        "future-action",
    ]
    items = [
        {"playlist_id": "inserted"},
        {"playlist_id": "updated"},
        {"playlist_id": "stale"},
        {},
        {"playlist_id": "future"},
    ]

    result = apply_pushed_playlists(db, items)

    assert result == PlaylistServiceResult(
        {
            "success": True,
            "received": 5,
            "accepted": 2,
            "stale": 1,
            "skipped": 2,
            "results": [
                {"playlist_id": "inserted", "result": "inserted"},
                {"playlist_id": "updated", "result": "updated"},
                {"playlist_id": "stale", "result": "stale"},
                {"playlist_id": None, "result": "skipped"},
                {"playlist_id": "future", "result": "future-action"},
            ],
        }
    )
    assert db.method_calls == [
        call.apply_pushed_playlist_row(item) for item in items
    ]


def test_apply_pushed_playlists_leaves_row_failures_for_route_translation():
    db = MagicMock()
    db.apply_pushed_playlist_row.side_effect = [
        "inserted",
        RuntimeError("playlist write failed"),
    ]
    items = [
        {"playlist_id": "first"},
        {"playlist_id": "second"},
        {"playlist_id": "never-reached"},
    ]

    with pytest.raises(RuntimeError, match="playlist write failed"):
        apply_pushed_playlists(db, items)

    assert db.method_calls == [
        call.apply_pushed_playlist_row(items[0]),
        call.apply_pushed_playlist_row(items[1]),
    ]


def test_delete_orphan_file_keeps_authorize_remove_clear_order():
    events = []
    store = OrphanStore(
        [{"mbid": "track-1", "local_path": "  /music/track.flac  "}],
        events,
    )

    def remove_file(path):
        events.append(("remove", path))

    result = delete_orphan_track_file(
        db_path="library.db",
        database_factory=orphan_factory(store, events),
        mbid="track-1",
        remove_file=remove_file,
    )

    assert result == LibraryApplicationResult(
        {
            "success": True,
            "mbid": "track-1",
            "path": "/music/track.flac",
            "removed": True,
        }
    )
    assert events == [
        ("db-factory", "library.db"),
        "db-enter",
        "get-orphans",
        ("remove", "/music/track.flac"),
        ("clear-path", "track-1", None),
        "db-exit",
    ]


def test_delete_orphan_file_treats_missing_file_as_idempotent():
    events = []
    store = OrphanStore(
        [{"mbid": "track-1", "local_path": "/missing.flac"}],
        events,
    )

    def remove_file(path):
        events.append(("remove", path))
        raise FileNotFoundError(path)

    result = delete_orphan_track_file(
        db_path="library.db",
        database_factory=orphan_factory(store, events),
        mbid="track-1",
        remove_file=remove_file,
    )

    assert result.payload == {
        "success": True,
        "mbid": "track-1",
        "path": "/missing.flac",
        "removed": False,
    }
    assert events[-2:] == [
        ("clear-path", "track-1", None),
        "db-exit",
    ]


def test_delete_orphan_file_with_blank_path_does_not_mutate_database():
    events = []
    store = OrphanStore(
        [{"mbid": "track-1", "local_path": "  "}],
        events,
    )
    remove_file = MagicMock()

    result = delete_orphan_track_file(
        db_path="library.db",
        database_factory=orphan_factory(store, events),
        mbid="track-1",
        remove_file=remove_file,
    )

    assert result.payload == {
        "success": True,
        "mbid": "track-1",
        "path": None,
        "removed": False,
    }
    remove_file.assert_not_called()
    assert not any(
        isinstance(event, tuple) and event[0] == "clear-path"
        for event in events
    )


def test_delete_orphan_file_rejects_live_track_before_filesystem_work():
    events = []
    store = OrphanStore([], events)
    remove_file = MagicMock()

    result = delete_orphan_track_file(
        db_path="library.db",
        database_factory=orphan_factory(store, events),
        mbid="live-track",
        remove_file=remove_file,
    )

    assert result == LibraryApplicationResult(
        {
            "success": False,
            "message": "track is not an orphan; soft-delete it first",
        },
        409,
    )
    remove_file.assert_not_called()
    assert events == [
        ("db-factory", "library.db"),
        "db-enter",
        "get-orphans",
        "db-exit",
    ]


def test_delete_orphan_file_translates_remove_failure_without_clearing_path():
    events = []
    store = OrphanStore(
        [{"mbid": "track-1", "local_path": "/protected.flac"}],
        events,
    )
    event_logger = MagicMock()

    result = delete_orphan_track_file(
        db_path="library.db",
        database_factory=orphan_factory(store, events),
        mbid="track-1",
        remove_file=MagicMock(side_effect=PermissionError("denied")),
        event_logger=event_logger,
    )

    assert result == LibraryApplicationResult(
        {"success": False, "message": "denied"},
        500,
    )
    assert not any(
        isinstance(event, tuple) and event[0] == "clear-path"
        for event in events
    )
    assert events[-1] == "db-exit"
    event_logger.error.assert_called_once_with(
        "delete_track_file(track-1) failed: denied",
        exc_info=True,
    )
