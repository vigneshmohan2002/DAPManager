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


@patch('mutagen.File')
@patch('shutil.move')
@patch('os.walk')
def test_process_success_with_files(mock_walk, mock_move, mock_mutagen, downloader, db):
    """Test processing success with files found."""
    # Setup mocks
    mock_walk.return_value = [("/tmp", [], ["test.flac"])]
    mock_audio = MagicMock()
    mock_audio.get.return_value = ['Test Artist']
    mock_mutagen.return_value = mock_audio

    # Stub the auto-tag step so we don't hit AcoustID/MusicBrainz from tests.
    downloader._auto_tag_file = MagicMock(
        return_value=(
            {
                'artist': 'Test Artist',
                'title': 'Test Song',
                'album': 'Test Album',
                'track_number': 1,
            },
            "green",
            0.95,
        )
    )

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


def _downloader_with_key(db, temp_dirs, scanner=None):
    return Downloader(
        db=db,
        scanner=scanner or MagicMock(),
        slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config={"acoustid_api_key": "KEY", "contact_email": "me@x"},
    )


def test_auto_tag_embedded_mbid_short_circuits_to_green(db, temp_dirs):
    """A file that already carries an MBID must be treated as green
    without hitting AcoustID at all."""
    dl = _downloader_with_key(db, temp_dirs)
    with patch.object(dl, "_file_has_embedded_mbid", return_value=True), \
         patch("src.downloader.tag_service.identify_file") as mock_identify:
        meta, tier, score = dl._auto_tag_file("/whatever.flac")
    assert tier == "green"
    assert meta is None    # caller falls back to the file's own tags
    assert score is None
    mock_identify.assert_not_called()


def test_auto_tag_green_match_writes_tags(db, temp_dirs):
    dl = _downloader_with_key(db, temp_dirs)
    candidate = {
        "score": 0.97,
        "tier": "green",
        "meta": {"artist": "A", "title": "T", "album": "Al"},
    }
    with patch.object(dl, "_file_has_embedded_mbid", return_value=False), \
         patch("src.downloader.tag_service.identify_file", return_value=candidate), \
         patch("src.downloader.tag_service.write_tags") as mock_write:
        meta, tier, score = dl._auto_tag_file("/some.flac")
    mock_write.assert_called_once_with("/some.flac", candidate["meta"])
    assert tier == "green"
    assert meta == candidate["meta"]
    assert score == 0.97


def test_auto_tag_yellow_match_does_not_write_and_flags(db, temp_dirs):
    """Yellow must NOT auto-apply — the whole point of the flag."""
    dl = _downloader_with_key(db, temp_dirs)
    candidate = {
        "score": 0.72,
        "tier": "yellow",
        "meta": {"artist": "A", "title": "T"},
    }
    with patch.object(dl, "_file_has_embedded_mbid", return_value=False), \
         patch("src.downloader.tag_service.identify_file", return_value=candidate), \
         patch("src.downloader.tag_service.write_tags") as mock_write:
        meta, tier, score = dl._auto_tag_file("/some.flac")
    mock_write.assert_not_called()
    assert tier == "yellow"
    assert meta is None
    assert score == 0.72


def test_auto_tag_red_match_does_not_write_and_flags(db, temp_dirs):
    dl = _downloader_with_key(db, temp_dirs)
    candidate = {
        "score": 0.3, "tier": "red",
        "meta": {"artist": "?", "title": "?"},
    }
    with patch.object(dl, "_file_has_embedded_mbid", return_value=False), \
         patch("src.downloader.tag_service.identify_file", return_value=candidate), \
         patch("src.downloader.tag_service.write_tags") as mock_write:
        _, tier, _ = dl._auto_tag_file("/some.flac")
    mock_write.assert_not_called()
    assert tier == "red"


def test_auto_tag_no_match_flags_red(db, temp_dirs):
    dl = _downloader_with_key(db, temp_dirs)
    with patch.object(dl, "_file_has_embedded_mbid", return_value=False), \
         patch("src.downloader.tag_service.identify_file", return_value=None):
        _, tier, score = dl._auto_tag_file("/some.flac")
    assert tier == "red"
    assert score is None


def test_auto_tag_without_api_key_is_noop(db, temp_dirs):
    dl = Downloader(
        db=db, scanner=MagicMock(), slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u", slsk_password="p",
        slsk_config={"acoustid_api_key": ""},
    )
    with patch.object(dl, "_file_has_embedded_mbid", return_value=False), \
         patch("src.downloader.tag_service.identify_file") as mock_id:
        meta, tier, score = dl._auto_tag_file("/some.flac")
    assert (meta, tier, score) == (None, None, None)
    mock_id.assert_not_called()


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


# --- Lidarr handoff -------------------------------------------------

def _album_item(release_mbid="rel-mb", query="::ALBUM:: Artist - Album", item_id=1):
    return DownloadItem(
        id=item_id,
        search_query=query,
        playlist_id="COMPLETER",
        mbid_guess=release_mbid,
    )


def _lidarr_ready_downloader(db, mock_scanner, temp_dirs):
    lidarr = MagicMock()
    return Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        lidarr_client=lidarr,
        lidarr_quality_profile_id=3,
        lidarr_root_folder_path="/music",
    ), lidarr


def test_try_lidarr_no_client_returns_false(downloader):
    assert downloader._try_lidarr_album(_album_item(), report=lambda m: None) is False


def test_try_lidarr_requires_profile_and_root(db, mock_scanner, temp_dirs):
    dl, _ = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    dl.lidarr_quality_profile_id = None
    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is False


def test_try_lidarr_skips_single_track_items(db, mock_scanner, temp_dirs):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    item = _album_item(query="Artist - Track")
    assert dl._try_lidarr_album(item, report=lambda m: None) is False
    lidarr.ensure_album_monitored.assert_not_called()


def test_try_lidarr_skips_when_no_release_mbid(db, mock_scanner, temp_dirs):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    item = _album_item(release_mbid="")
    assert dl._try_lidarr_album(item, report=lambda m: None) is False
    lidarr.ensure_album_monitored.assert_not_called()


def test_try_lidarr_success_returns_true(db, mock_scanner, temp_dirs):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    lidarr.ensure_album_monitored.return_value = {"id": 42}
    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is True
    lidarr.ensure_album_monitored.assert_called_once_with(
        "rel-mb", quality_profile_id=3, root_folder_path="/music"
    )


def test_try_lidarr_lookup_miss_returns_false(db, mock_scanner, temp_dirs):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    lidarr.ensure_album_monitored.return_value = None
    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is False


def test_try_lidarr_swallows_lidarr_errors(db, mock_scanner, temp_dirs):
    from src.lidarr_client import LidarrError
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    lidarr.ensure_album_monitored.side_effect = LidarrError("nope")
    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is False


def test_run_queue_skips_sldl_when_lidarr_accepts(db, mock_scanner, temp_dirs):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="COMPLETER",
        mbid_guess="rel-mb",
    ))
    lidarr.ensure_album_monitored.return_value = {"id": 77}

    with patch.object(dl, "_attempt_download") as attempt:
        dl.run_queue()

    attempt.assert_not_called()
    # Item should be gone from the queue after Lidarr handoff
    assert db.get_downloads(status="pending") == []


# --- main_run_downloader / Lidarr client wiring ---------------------

def test_build_lidarr_client_skipped_on_satellite():
    from src.downloader import _build_lidarr_client
    config = {
        "is_master": False,
        "lidarr_enabled": True,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_disabled():
    from src.downloader import _build_lidarr_client
    config = {
        "is_master": True,
        "lidarr_enabled": False,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_creds_missing():
    from src.downloader import _build_lidarr_client
    config = {"is_master": True, "lidarr_enabled": True, "lidarr_url": "", "lidarr_api_key": "k"}
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_ping_fails():
    from src.downloader import _build_lidarr_client
    config = {
        "is_master": True,
        "lidarr_enabled": True,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    with patch("src.downloader.LidarrClient") as cls:
        instance = MagicMock()
        instance.ping.return_value = False
        cls.return_value = instance
        assert _build_lidarr_client(config) is None


def test_build_lidarr_client_happy_path():
    from src.downloader import _build_lidarr_client
    config = {
        "is_master": True,
        "lidarr_enabled": True,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    with patch("src.downloader.LidarrClient") as cls:
        instance = MagicMock()
        instance.ping.return_value = True
        cls.return_value = instance
        assert _build_lidarr_client(config) is instance