import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch
from src.downloader import Downloader, main_run_downloader
from src.db_manager import DatabaseManager, DownloadItem, Track


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield {
            "root": temp_dir,
            "downloads": os.path.join(temp_dir, "downloads"),
            "music_library": os.path.join(temp_dir, "music")
        }


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def mock_scanner():
    """Create mock scanner."""
    return MagicMock()


@pytest.fixture
def downloader(db, mock_scanner, temp_dirs):
    """Create downloader instance for testing."""
    return Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="test_user",
        slsk_password="test_pass"
    )


def test_downloader_initialization(downloader, temp_dirs):
    """Test downloader initialization."""
    assert downloader.slsk_cmd_base == ["slsk-batchdl"]
    assert downloader.downloads_dir == temp_dirs["downloads"]
    assert downloader.music_library_dir == temp_dirs["music_library"]
    assert downloader.slsk_username == "test_user"
    assert downloader.slsk_password == "test_pass"


@patch('subprocess.Popen')
def test_attempt_download_success(mock_popen, downloader):
    """Test successful download attempt."""
    # Setup mock
    mock_process = MagicMock()
    mock_process.stdout = []
    mock_process.wait.return_value = None
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    # Create download item
    item = DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid"
    )

    # Test download attempt
    result = downloader._attempt_download(item)
    assert result is True
    mock_popen.assert_called_once()


@patch('subprocess.Popen')
def test_attempt_download_failure(mock_popen, downloader):
    """Test failed download attempt."""
    # Setup mock to raise exception
    mock_popen.side_effect = FileNotFoundError("Command not found")

    # Create download item
    item = DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid"
    )

    # Test download attempt
    with pytest.raises(FileNotFoundError):
        downloader._attempt_download(item)


def test_process_failure(downloader, db):
    """Test processing a failed download."""
    # Create download item
    item = DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid",
        status="pending"
    )
    db.queue_download(item)

    # Get the item with ID
    items = db.get_all_downloads()
    test_item = items[0]

    # Process failure
    downloader._process_failure(test_item, "Test error")

    # Check status was updated
    updated_items = db.get_all_downloads()
    assert updated_items[0].status == "failed"


def test_get_library_path_for_track(downloader):
    """Test library path generation."""
    track = Track(
        mbid="test_mbid",
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        track_number=1
    )

    path = downloader._get_library_path_for_track(track)
    assert "Test Artist" in path
    assert "Test Album" in path
    assert "01 Test Song.flac" in path


@patch('os.walk')
def test_process_success_no_files(mock_walk, downloader, db):
    """Test processing success with no files found."""
    # Setup mock
    mock_walk.return_value = []

    # Create download item
    item = DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid"
    )

    # Process success
    downloader._process_success(item)

    # Verify item was removed from queue
    items = db.get_all_downloads()
    assert len(items) == 0


@patch('src.downloader.AutoTagger')
@patch('mutagen.File')
@patch('shutil.move')
@patch('os.walk')
def test_process_success_with_files(mock_walk, mock_move, mock_mutagen, mock_autotagger, downloader, db):
    """Test processing success with files found."""
    # Setup mocks
    mock_walk.return_value = [("/tmp", [], ["test.flac"])]
    mock_audio = MagicMock()
    mock_audio.get.return_value = ['Test Artist']
    mock_mutagen.return_value = mock_audio

    # Mock autotagger
    mock_tagger = MagicMock()
    mock_tagger.identify_and_tag.return_value = {
        'artist': 'Test Artist',
        'title': 'Test Song',
        'album': 'Test Album',
        'track_number': 1
    }
    downloader.auto_tagger = mock_tagger

    # Create track-like object for path generation
    track = MagicMock()
    track.artist = "Test Artist"
    track.album = "Test Album"
    track.title = "Test Song"
    track.track_number = 1

    # Create download item
    item = DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid"
    )

    # Process success
    downloader._process_success(item)

    # Verify item was removed from queue
    items = db.get_all_downloads()
    assert len(items) == 0


def test_main_run_downloader(db, temp_dirs):
    """Test main downloader function."""
    config = {
        "slsk_cmd_base": ["slsk-batchdl"],
        "downloads_path": temp_dirs["downloads"],
        "music_library_path": temp_dirs["music_library"],
        "slsk_username": "test_user",
        "slsk_password": "test_pass"
    }

    with patch('src.downloader.Downloader') as mock_downloader_class, \
         patch('src.downloader.LibraryScanner') as mock_scanner_class:
        mock_downloader = MagicMock()
        mock_downloader_class.return_value = mock_downloader

        main_run_downloader(db, config)

        mock_downloader_class.assert_called_once()
        mock_downloader.run_queue.assert_called_once()