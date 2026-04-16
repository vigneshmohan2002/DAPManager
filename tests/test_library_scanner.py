import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch
from src.library_scanner import LibraryScanner, main_scan_library
from src.db_manager import DatabaseManager, Track


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield {
            "root": temp_dir,
            "music_library": os.path.join(temp_dir, "music")
        }


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def scanner(db):
    """Create library scanner instance for testing."""
    with patch('src.library_scanner.get_config') as mock_config:
        mock_config.return_value.picard_path = "picard"
        return LibraryScanner(db)


def test_scanner_initialization(scanner):
    """Test scanner initialization."""
    assert scanner.db is not None
    assert isinstance(scanner.resolved_albums, set)


@patch('os.path.exists')
def test_scan_library_nonexistent_path(mock_exists, scanner):
    """Test scanning nonexistent library path."""
    mock_exists.return_value = False
    scanner.scan_library("/nonexistent/path")
    # Should not raise exception and should return early


@patch('os.walk')
@patch('os.path.exists')
def test_scan_library_empty_directory(mock_exists, mock_walk, scanner):
    """Test scanning empty directory."""
    mock_exists.return_value = True
    mock_walk.return_value = []

    scanner.scan_library("/empty/path")
    # Should not raise exception


def test_fetch_release_info_from_api_success(scanner):
    """Test successful release info fetch."""
    with patch('src.musicbrainz_client.musicbrainzngs') as mock_mb:
        mock_mb.get_recording_by_id.return_value = {
            'recording': {
                'release-list': [{'id': 'release1', 'title': 'Test Album', 'status': 'Official'}]
            }
        }
        mock_mb.get_release_by_id.return_value = {
            'release': {
                'medium-list': [{'track-count': '10'}]
            }
        }

        release_id, track_count = scanner._fetch_release_info_from_api("test_mbid", "Test Album")
        assert release_id == "release1"
        assert track_count == 10


def test_fetch_release_info_from_api_failure(scanner):
    """Test failed release info fetch."""
    with patch('src.musicbrainz_client.musicbrainzngs') as mock_mb:
        mock_mb.get_recording_by_id.side_effect = Exception("API Error")

        release_id, track_count = scanner._fetch_release_info_from_api("test_mbid", "Test Album")
        assert release_id is None
        assert track_count == 0


@patch('src.library_scanner.MediaFile')
def test_process_file_skipped_existing(mock_mediafile, scanner, db):
    """Test processing file that already exists in DB."""
    # Setup existing track
    existing_track = Track(
        mbid="test_mbid",
        title="Existing Song",
        artist="Existing Artist",
        album="Existing Album",
        local_path="/existing/path.flac"
    )
    db.add_or_update_track(existing_track)

    # Mock MediaFile to return same MBID
    mock_file = MagicMock()
    mock_file.mb_trackid = "test_mbid"
    mock_mediafile.return_value = mock_file

    # Process file
    result = scanner._process_file("/test/path.flac")
    assert result == "skipped"


@patch('src.library_scanner.MediaFile')
def test_process_file_processed_new(mock_mediafile, scanner, db):
    """Test processing new file."""
    # Mock MediaFile
    mock_file = MagicMock()
    mock_file.mb_trackid = "new_mbid"
    mock_file.title = "New Song"
    mock_file.artist = "New Artist"
    mock_file.album = "New Album"
    mock_file.track = 1
    mock_file.disc = 1
    mock_file.mb_albumid = "album_mbid"
    mock_file.tracktotal = 10
    mock_mediafile.return_value = mock_file

    with patch.object(scanner, '_fetch_release_info_from_api') as mock_fetch:
        mock_fetch.return_value = ("album_mbid", 10)

        # Process file
        result = scanner._process_file("/test/new_path.flac")
        assert result == "processed"

        # Verify track was added to DB
        track = db.get_track_by_mbid("new_mbid")
        assert track is not None
        assert track.title == "New Song"


@patch('src.library_scanner.MediaFile')
def test_process_file_skipped_no_mbid(mock_mediafile, scanner):
    """Test processing file with no MBID."""
    # Mock MediaFile without MBID
    mock_file = MagicMock()
    mock_file.mb_trackid = None
    mock_mediafile.return_value = mock_file

    # Mock picard tagger to also fail
    with patch.object(scanner, '_run_picard_tagger') as mock_picard:
        mock_picard.return_value = False

        # Process file
        result = scanner._process_file("/test/no_mbid.flac")
        assert result == "skipped"


def test_run_picard_tagger(scanner):
    """Test running picard tagger."""
    with patch('src.library_scanner.find_mbid_by_fingerprint') as mock_find:
        mock_find.return_value = "found_mbid"

        result = scanner._run_picard_tagger("/test/path.flac")
        assert result == "found_mbid"


def test_main_scan_library(db, temp_dirs):
    """Test main scan library function."""
    config = {"music_library_path": temp_dirs["music_library"]}

    with patch('src.library_scanner.LibraryScanner') as mock_scanner_class:
        mock_scanner = MagicMock()
        mock_scanner_class.return_value = mock_scanner

        main_scan_library(db, config)

        mock_scanner_class.assert_called_once_with(db)
        mock_scanner.scan_library.assert_called_once_with(temp_dirs["music_library"])