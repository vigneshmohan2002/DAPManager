import pytest
from unittest.mock import MagicMock, patch
from src.album_completer import (
    fetch_album_tracklist,
    get_missing_tracks_for_album,
    queue_missing_tracks_for_album,
    complete_albums,
    discover_album_for_track,
    audit_library,
)
from src.db_manager import DatabaseManager, Track, DownloadItem


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


def _add_track(db, mbid="t1", title="Song", artist="Artist", album="Album",
               release_mbid="r1", track_number=1, disc_number=1, local_path="/music/song.flac"):
    track = Track(
        mbid=mbid, title=title, artist=artist, album=album,
        local_path=local_path, release_mbid=release_mbid,
        track_number=track_number, disc_number=disc_number,
    )
    db.add_or_update_track(track)
    return track


# ---------------------------------------------------------------------------
# fetch_album_tracklist
# ---------------------------------------------------------------------------

def test_fetch_album_tracklist_success():
    with patch("src.musicbrainz_client.musicbrainzngs") as mock_mb:
        mock_mb.get_release_by_id.return_value = {
            "release": {
                "medium-list": [
                    {
                        "position": "1",
                        "track-list": [
                            {"number": "1", "recording": {"title": "Track 1"}},
                            {"number": "2", "recording": {"title": "Track 2"}},
                        ],
                    }
                ]
            }
        }

        result = fetch_album_tracklist("test_mbid")
        assert len(result) == 2
        assert result[(1, 1)] == "Track 1"
        assert result[(1, 2)] == "Track 2"


def test_fetch_album_tracklist_multi_disc():
    with patch("src.musicbrainz_client.musicbrainzngs") as mock_mb:
        mock_mb.get_release_by_id.return_value = {
            "release": {
                "medium-list": [
                    {"position": "1", "track-list": [
                        {"number": "1", "recording": {"title": "D1T1"}},
                    ]},
                    {"position": "2", "track-list": [
                        {"number": "1", "recording": {"title": "D2T1"}},
                    ]},
                ]
            }
        }

        result = fetch_album_tracklist("multi_disc")
        assert result[(1, 1)] == "D1T1"
        assert result[(2, 1)] == "D2T1"


def test_fetch_album_tracklist_api_error():
    with patch("src.musicbrainz_client.musicbrainzngs") as mock_mb:
        mock_mb.get_release_by_id.side_effect = Exception("API Error")
        assert fetch_album_tracklist("bad_mbid") == {}


# ---------------------------------------------------------------------------
# get_missing_tracks_for_album
# ---------------------------------------------------------------------------

def test_get_missing_no_local_tracks(db):
    result = get_missing_tracks_for_album(db, "nonexistent")
    assert "error" in result


def test_get_missing_finds_gaps(db):
    _add_track(db, mbid="t1", track_number=1, release_mbid="r1")

    with patch("src.album_completer.fetch_album_tracklist") as mock_fetch:
        mock_fetch.return_value = {
            (1, 1): "Track 1",
            (1, 2): "Track 2",
            (1, 3): "Track 3",
        }
        result = get_missing_tracks_for_album(db, "r1")

    assert result["total_tracks"] == 3
    assert result["have"] == 1
    assert result["missing_count"] == 2
    assert len(result["missing_tracks"]) == 2
    titles = [t["title"] for t in result["missing_tracks"]]
    assert "Track 2" in titles
    assert "Track 3" in titles


def test_get_missing_album_complete(db):
    _add_track(db, mbid="t1", track_number=1, release_mbid="r1")
    _add_track(db, mbid="t2", title="Song 2", track_number=2, release_mbid="r1",
               local_path="/music/song2.flac")

    with patch("src.album_completer.fetch_album_tracklist") as mock_fetch:
        mock_fetch.return_value = {(1, 1): "Song", (1, 2): "Song 2"}
        result = get_missing_tracks_for_album(db, "r1")

    assert result["missing_count"] == 0


# ---------------------------------------------------------------------------
# queue_missing_tracks_for_album
# ---------------------------------------------------------------------------

def test_queue_missing_error_handling():
    with patch("src.album_completer.get_missing_tracks_for_album") as mock_get:
        mock_get.return_value = {"error": "Test error"}
        result = queue_missing_tracks_for_album(MagicMock(), "bad_mbid")
        assert "error" in result


def test_queue_individual_tracks(db):
    """When only a small fraction is missing, individual tracks are queued."""
    # 8-track album, have 7 -- only 1 missing (12.5%), so individual mode
    for i in range(1, 8):
        _add_track(db, mbid=f"t{i}", title=f"Track {i}", track_number=i,
                   release_mbid="r1", local_path=f"/music/track{i}.flac")
    db.update_album_metadata("r1", "Album", 8)

    with patch("src.album_completer.fetch_album_tracklist") as mock_fetch:
        mock_fetch.return_value = {(1, i): f"Track {i}" for i in range(1, 9)}
        result = queue_missing_tracks_for_album(db, "r1")

    assert result["queued"] == 1
    assert result["skipped_existing"] == 0

    pending = db.get_downloads("pending")
    assert len(pending) == 1
    assert "Track 8" in pending[0].search_query


def test_queue_entire_album_when_mostly_missing(db):
    """When >60% of tracks are missing, should queue the whole album."""
    _add_track(db, mbid="t1", track_number=1, release_mbid="r1")
    db.update_album_metadata("r1", "Album", 10)

    with patch("src.album_completer.fetch_album_tracklist") as mock_fetch:
        mock_fetch.return_value = {(1, i): f"Track {i}" for i in range(1, 11)}
        result = queue_missing_tracks_for_album(db, "r1")

    assert result["queued"] == 1
    pending = db.get_downloads("pending")
    assert len(pending) == 1
    assert pending[0].search_query.startswith("::ALBUM::")


def test_queue_skips_already_queued(db):
    """Pre-queued tracks should be skipped."""
    # 8-track album, have 6 -- missing 2 (25%), individual mode
    for i in range(1, 7):
        _add_track(db, mbid=f"t{i}", title=f"Track {i}", track_number=i,
                   release_mbid="r1", local_path=f"/music/track{i}.flac")
    db.update_album_metadata("r1", "Album", 8)

    # Pre-queue one of the missing tracks
    db.queue_download(DownloadItem(
        search_query="Artist - Track 7", playlist_id="TEST",
        mbid_guess="", status="pending",
    ))

    with patch("src.album_completer.fetch_album_tracklist") as mock_fetch:
        mock_fetch.return_value = {(1, i): f"Track {i}" for i in range(1, 9)}
        result = queue_missing_tracks_for_album(db, "r1")

    assert result["queued"] == 1       # Track 8
    assert result["skipped_existing"] == 1  # Track 7 already queued


# ---------------------------------------------------------------------------
# discover_album_for_track
# ---------------------------------------------------------------------------

def test_discover_album_success(db):
    with patch("src.musicbrainz_client.musicbrainzngs") as mock_mb:
        mock_mb.get_recording_by_id.return_value = {
            "recording": {
                "release-list": [
                    {"id": "rel_1", "title": "My Album", "status": "Official"}
                ]
            }
        }
        mock_mb.get_release_by_id.return_value = {
            "release": {
                "medium-list": [{"track-count": "12"}]
            }
        }

        result = discover_album_for_track(db, "rec_1", album_hint="My Album")
        assert result == "rel_1"


def test_discover_album_no_releases(db):
    with patch("src.musicbrainz_client.musicbrainzngs") as mock_mb:
        mock_mb.get_recording_by_id.return_value = {
            "recording": {"release-list": []}
        }
        assert discover_album_for_track(db, "rec_1") is None


# ---------------------------------------------------------------------------
# complete_albums (full pipeline)
# ---------------------------------------------------------------------------

def test_complete_albums_no_work(db):
    """When library is empty, should complete quickly."""
    summary = complete_albums(db)
    assert summary["incomplete_albums"] == 0
    assert summary["tracks_queued"] == 0


def test_complete_albums_discovers_and_queues(db):
    # Add a track with no release_mbid (orphan)
    _add_track(db, mbid="orphan1", release_mbid=None, local_path="/m/orphan.flac")

    # Add a track with release_mbid but incomplete album (1/3 = 66% missing -> full album mode)
    _add_track(db, mbid="t1", track_number=1, release_mbid="r1", local_path="/m/t1.flac")
    db.update_album_metadata("r1", "Album", 3)

    with patch("src.album_completer.discover_album_for_track") as mock_discover, \
         patch("src.album_completer.fetch_album_tracklist") as mock_fetch:

        mock_discover.return_value = "r2"
        mock_fetch.return_value = {(1, 1): "T1", (1, 2): "T2", (1, 3): "T3"}

        summary = complete_albums(db)

    assert summary["albums_discovered"] == 1
    assert summary["incomplete_albums"] >= 1
    # 66% missing triggers full-album mode: 1 queue entry per incomplete album
    assert summary["tracks_queued"] >= 1


# ---------------------------------------------------------------------------
# audit_library
# ---------------------------------------------------------------------------

def test_audit_empty_library(db):
    result = audit_library(db)
    assert result == []


def test_audit_finds_incomplete(db):
    _add_track(db, mbid="t1", track_number=1, release_mbid="r1")
    db.update_album_metadata("r1", "Album", 5)

    result = audit_library(db)
    assert len(result) == 1
    assert result[0]["missing"] == 4
