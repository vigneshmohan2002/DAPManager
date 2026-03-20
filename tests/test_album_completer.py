import pytest
from unittest.mock import MagicMock, patch
from src.album_completer import fetch_album_tracklist, get_missing_tracks_for_album, queue_missing_tracks_for_album
from src.db_manager import DatabaseManager, Track


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


def test_fetch_album_tracklist_success():
    """Test successful album tracklist fetch."""
    with patch('src.album_completer.musicbrainzngs') as mock_mb:
        mock_mb.get_release_by_id.return_value = {
            'release': {
                'medium-list': [
                    {
                        'position': '1',
                        'track-list': [
                            {
                                'number': '1',
                                'recording': {'title': 'Track 1'}
                            },
                            {
                                'number': '2',
                                'recording': {'title': 'Track 2'}
                            }
                        ]
                    }
                ]
            }
        }

        track_map = fetch_album_tracklist("test_mbid")
        assert len(track_map) == 2
        assert track_map[(1, 1)] == "Track 1"
        assert track_map[(1, 2)] == "Track 2"


def test_fetch_album_tracklist_failure():
    """Test failed album tracklist fetch."""
    with patch('src.album_completer.musicbrainzngs') as mock_mb:
        mock_mb.get_release_by_id.side_effect = Exception("API Error")

        track_map = fetch_album_tracklist("test_mbid")
        assert track_map == {}


def test_get_missing_tracks_for_album_error(db):
    """Test getting missing tracks for album that doesn't exist."""
    with patch('src.album_completer.fetch_album_tracklist') as mock_fetch:
        result = get_missing_tracks_for_album(db, "nonexistent_mbid")
        assert 'error' in result


def test_get_missing_tracks_for_album_success(db):
    """Test successful missing tracks retrieval."""
    # Add a track to database
    track = Track(
        mbid="test_mbid",
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        local_path="/test/path.flac",
        release_mbid="test_mbid",
        track_number=1,
        disc_number=1
    )
    db.add_or_update_track(track)

    with patch('src.album_completer.fetch_album_tracklist') as mock_fetch:
        # Setup mock data
        mock_fetch.return_value = {
            (1, 1): "Track 1",
            (1, 2): "Track 2",
            (1, 3): "Track 3"
        }

        result = get_missing_tracks_for_album(db, "test_mbid")

        # Should have the data structure but we're mainly testing no exceptions
        assert isinstance(result, dict)
        assert 'artist' in result
        assert 'album' in result
        assert 'mbid' in result


def test_queue_missing_tracks_for_album():
    """Test queueing missing tracks for album."""
    with patch('src.album_completer.get_missing_tracks_for_album') as mock_get:
        mock_get.return_value = {
            'error': 'Test error'
        }

        # Should handle error gracefully
        queue_missing_tracks_for_album(None, "test_mbid")  # Should not raise exception