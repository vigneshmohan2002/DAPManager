from unittest.mock import MagicMock, call

import pytest

from src.services.album_task_service import (
    AlbumCompletionResult,
    AuditQueueResult,
    queue_audit_downloads,
    run_album_completion_pipeline,
)


class TrackingContext:
    def __init__(self, store, events):
        self.store = store
        self.events = events

    def __enter__(self):
        self.events.append(("db-enter", self.store))
        return self.store

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append(("db-exit", self.store, exc_value))


def database_factory(events):
    stores = iter(("completion-db", "download-db", "scan-db"))

    def factory(db_path):
        store = next(stores)
        events.append(("db-factory", db_path, store))
        return TrackingContext(store, events)

    return factory


def test_album_completion_runs_three_fresh_contexts_and_phase_events():
    events = []
    config_values = {"music_library_path": "/music"}

    def progress(event):
        events.append(("progress", event))

    def complete(db, progress_callback=None):
        events.append(("complete", db, progress_callback))
        return {"tracks_queued": 2, "errors": 0, "details": []}

    def download(db, config, progress_callback=None):
        events.append(("download", db, config, progress_callback))

    def scan(db, config):
        events.append(("scan", db, config))

    result = run_album_completion_pipeline(
        db_path="library.db",
        config_values=config_values,
        run_downloads=True,
        progress_callback=progress,
        database_factory=database_factory(events),
        complete_albums=complete,
        run_downloader=download,
        scan_library=scan,
    )

    assert result == AlbumCompletionResult(
        summary={"tracks_queued": 2, "errors": 0, "details": []},
        downloads_run=True,
        rescan_run=True,
    )
    assert events == [
        ("db-factory", "library.db", "completion-db"),
        ("db-enter", "completion-db"),
        ("complete", "completion-db", progress),
        ("db-exit", "completion-db", None),
        ("progress", {"message": "Downloading queued tracks..."}),
        ("db-factory", "library.db", "download-db"),
        ("db-enter", "download-db"),
        ("download", "download-db", config_values, progress),
        ("db-exit", "download-db", None),
        ("progress", {"message": "Re-scanning library..."}),
        ("db-factory", "library.db", "scan-db"),
        ("db-enter", "scan-db"),
        ("scan", "scan-db", config_values),
        ("db-exit", "scan-db", None),
    ]


@pytest.mark.parametrize(
    ("run_downloads", "tracks_queued"),
    ((False, 3), (True, 0)),
)
def test_album_completion_skips_download_and_rescan_unless_both_conditions_hold(
    run_downloads,
    tracks_queued,
):
    events = []
    progress = MagicMock()
    download = MagicMock()
    scan = MagicMock()

    result = run_album_completion_pipeline(
        db_path="library.db",
        config_values={},
        run_downloads=run_downloads,
        progress_callback=progress,
        database_factory=database_factory(events),
        complete_albums=lambda db, progress_callback=None: {
            "tracks_queued": tracks_queued
        },
        run_downloader=download,
        scan_library=scan,
    )

    assert result.downloads_run is False
    assert result.rescan_run is False
    assert [event[0] for event in events] == [
        "db-factory",
        "db-enter",
        "db-exit",
    ]
    progress.assert_not_called()
    download.assert_not_called()
    scan.assert_not_called()


def test_album_completion_propagates_download_error_without_rescan():
    events = []

    def download(db, config, progress_callback=None):
        events.append(("download", db))
        raise RuntimeError("download failed")

    scan = MagicMock()
    with pytest.raises(RuntimeError, match="download failed"):
        run_album_completion_pipeline(
            db_path="library.db",
            config_values={},
            run_downloads=True,
            database_factory=database_factory(events),
            complete_albums=lambda db, progress_callback=None: {
                "tracks_queued": 1
            },
            run_downloader=download,
            scan_library=scan,
        )

    assert events[-1][:2] == ("db-exit", "download-db")
    assert isinstance(events[-1][2], RuntimeError)
    assert str(events[-1][2]) == "download failed"
    scan.assert_not_called()


def test_audit_queue_album_mode_uses_marker_and_ignores_track_items():
    db = MagicMock()
    item_factory = MagicMock(return_value="album-download")

    result = queue_audit_downloads(
        db,
        items=[{"artist": "Ignored", "title": "Ignored"}],
        queue_album="truthy",
        album_info={
            "artist": "Album Artist",
            "album": "Album Title",
            "release_mbid": "release-1",
        },
        item_factory=item_factory,
    )

    assert result == AuditQueueResult(
        {"success": True, "queued_count": 1}
    )
    item_factory.assert_called_once_with(
        search_query="::ALBUM:: Album Artist - Album Title",
        playlist_id="AUDIT",
        mbid_guess="release-1",
        status="pending",
    )
    db.queue_download.assert_called_once_with("album-download")


def test_audit_queue_album_mode_preserves_missing_value_format_and_mbid_default():
    db = MagicMock()
    item_factory = MagicMock(return_value="album-download")

    queue_audit_downloads(
        db,
        items=[],
        queue_album=True,
        album_info={},
        item_factory=item_factory,
    )

    item_factory.assert_called_once_with(
        search_query="::ALBUM:: None - None",
        playlist_id="AUDIT",
        mbid_guess="",
        status="pending",
    )


def test_audit_queue_track_mode_mutates_in_order_and_counts_successes():
    events = []

    class Store:
        def queue_download(self, item):
            events.append(("queue", item))
            return len(events)

    def item_factory(**values):
        item = dict(values)
        events.append(("factory", item))
        return item

    result = queue_audit_downloads(
        Store(),
        items=[
            {"artist": "First Artist", "title": "First Track"},
            {"artist": "Second Artist", "title": "Second Track"},
        ],
        queue_album=False,
        album_info={"artist": "Ignored"},
        item_factory=item_factory,
    )

    assert result.payload == {"success": True, "queued_count": 2}
    expected_first = {
        "search_query": "First Artist - First Track",
        "playlist_id": "AUDIT",
        "mbid_guess": "",
        "status": "pending",
    }
    expected_second = {
        "search_query": "Second Artist - Second Track",
        "playlist_id": "AUDIT",
        "mbid_guess": "",
        "status": "pending",
    }
    assert events == [
        ("factory", expected_first),
        ("queue", expected_first),
        ("factory", expected_second),
        ("queue", expected_second),
    ]


def test_audit_queue_stops_on_first_mutation_error():
    db = MagicMock()
    db.queue_download.side_effect = [1, RuntimeError("queue failed")]
    item_factory = MagicMock(side_effect=lambda **values: values)
    items = [
        {"artist": "One", "title": "Track"},
        {"artist": "Two", "title": "Track"},
        {"artist": "Three", "title": "Track"},
    ]

    with pytest.raises(RuntimeError, match="queue failed"):
        queue_audit_downloads(
            db,
            items=items,
            queue_album=False,
            album_info={},
            item_factory=item_factory,
        )

    assert item_factory.call_args_list == [
        call(
            search_query="One - Track",
            playlist_id="AUDIT",
            mbid_guess="",
            status="pending",
        ),
        call(
            search_query="Two - Track",
            playlist_id="AUDIT",
            mbid_guess="",
            status="pending",
        ),
    ]
    assert db.queue_download.call_count == 2
