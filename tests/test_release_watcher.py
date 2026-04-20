"""Tests for src.release_watcher.run_watch_tick."""

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from src.db_manager import DatabaseManager
from src.lidarr_client import LidarrError
from src.release_watcher import _build_search_query, run_watch_tick


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rw.db")
        with DatabaseManager(path) as d:
            yield d


def _album(mbid, artist="Boards of Canada", title="Geogaddi"):
    return {
        "foreignAlbumId": mbid,
        "title": title,
        "artist": {"artistName": artist},
    }


def test_build_search_query():
    assert _build_search_query(_album("x")) == "Boards of Canada - Geogaddi"


def test_build_search_query_missing_artist_returns_just_title():
    album = {"title": "Geogaddi", "artist": {}}
    assert _build_search_query(album) == "Geogaddi"


def test_build_search_query_all_empty_is_empty():
    assert _build_search_query({"title": "", "artist": {}}) == ""


def test_queues_new_albums(db, monkeypatch):
    # Force the master path so queue_or_forward hits the local DB.
    monkeypatch.setattr(
        "src.release_watcher.queue_or_forward",
        lambda database, item: database.queue_download(item),
    )
    lidarr = MagicMock()
    lidarr.get_wanted_missing.return_value = [
        _album("mbid-1", "A1", "T1"),
        _album("mbid-2", "A2", "T2"),
    ]

    queued = run_watch_tick(db, lidarr)

    assert queued == 2
    assert db.has_queued_mbid("mbid-1") is True
    assert db.has_queued_mbid("mbid-2") is True


def test_dedups_across_ticks(db, monkeypatch):
    monkeypatch.setattr(
        "src.release_watcher.queue_or_forward",
        lambda database, item: database.queue_download(item),
    )
    lidarr = MagicMock()
    lidarr.get_wanted_missing.return_value = [_album("mbid-1")]

    assert run_watch_tick(db, lidarr) == 1
    # Second tick sees the same album — must not re-queue.
    assert run_watch_tick(db, lidarr) == 0


def test_skips_albums_without_mbid(db, monkeypatch):
    monkeypatch.setattr(
        "src.release_watcher.queue_or_forward",
        lambda database, item: database.queue_download(item),
    )
    lidarr = MagicMock()
    lidarr.get_wanted_missing.return_value = [
        {"foreignAlbumId": "", "title": "T", "artist": {"artistName": "A"}},
        _album("mbid-ok"),
    ]

    queued = run_watch_tick(db, lidarr)
    assert queued == 1
    assert db.has_queued_mbid("mbid-ok") is True


def test_skips_albums_without_query(db, monkeypatch):
    monkeypatch.setattr(
        "src.release_watcher.queue_or_forward",
        lambda database, item: database.queue_download(item),
    )
    lidarr = MagicMock()
    lidarr.get_wanted_missing.return_value = [
        {"foreignAlbumId": "mbid-x", "title": "", "artist": {"artistName": ""}},
    ]

    assert run_watch_tick(db, lidarr) == 0
    assert db.has_queued_mbid("mbid-x") is False


def test_lidarr_error_is_swallowed(db):
    lidarr = MagicMock()
    lidarr.get_wanted_missing.side_effect = LidarrError("down")

    # Must not raise — scheduler needs to keep ticking.
    assert run_watch_tick(db, lidarr) == 0
