import pytest
import os
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch
from mutagen.flac import FLAC
from src.downloader import (
    Downloader,
    ProcessedDownload,
    ProcessedQueueItem,
    build_download_command,
    cleanup_empty_download_directories,
    discover_downloaded_audio,
    exact_manifest_tag_metadata,
    main_run_downloader,
    parse_download_query,
    primary_artist_album_fallback_query,
    read_downloaded_metadata,
)
from src.db_manager import DatabaseManager, DownloadItem, Track
from src import tag_service


SATELLITE_RECORDINGS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
)


def _write_tagged_flac(path, recording_mbid, release_mbid):
    data = bytearray(b"fLaC")
    data += bytes([0x80, 0x00, 0x00, 0x22])
    data += (4096).to_bytes(2, "big") * 2
    data += (0).to_bytes(3, "big") * 2
    data += ((44100 << 44) | (15 << 36)).to_bytes(8, "big")
    data += b"\x00" * 16
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    audio = FLAC(str(path))
    audio["artist"] = "Artist"
    audio["album"] = "Album"
    audio["title"] = "Track"
    audio["musicbrainz_trackid"] = recording_mbid
    audio["musicbrainz_albumid"] = release_mbid
    audio.save()


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


def test_downloaded_metadata_accepts_composite_flac_track_number(tmp_path):
    path = tmp_path / "track.flac"
    _write_tagged_flac(
        path,
        SATELLITE_RECORDINGS[0],
        "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
    )
    audio = FLAC(str(path))
    audio["tracknumber"] = "1/12"
    audio["discnumber"] = "2/3"
    audio.save()

    metadata = read_downloaded_metadata(str(path), None)

    assert metadata is not None
    assert metadata.track_number == 1
    assert metadata.disc_number == 2


def test_build_download_command_preserves_single_track_flags():
    command, album_mode = build_download_command(
        ["sldl"],
        "user",
        "pass",
        "Artist - Song",
        "/downloads",
        "/music",
        {
            "fast_search": True,
            "remove_ft": True,
            "desperate_mode": True,
            "strict_quality": True,
        },
    )

    assert album_mode is False
    assert command == [
        "sldl", "--user", "user", "--pass", "pass", "--input",
        "Artist - Song", "-p", "/downloads", "--format", "flac",
        "--fast-search", "--remove-ft", "--desperate",
        "--strict-conditions", "--pref-format", "flac,wav",
    ]


def test_build_download_command_preserves_album_mode():
    assert parse_download_query("::ALBUM:: Artist - Album") == (
        "Artist - Album", True
    )
    command, album_mode = build_download_command(
        ["sldl"], "u", "p", "::ALBUM:: Artist - Album",
        "/downloads", "/music", {},
    )

    assert album_mode is True
    assert command[-5:] == [
        "--album",
        "--skip-music-dir",
        "/music",
        "--format",
        "flac",
    ]
    assert "--no-browse-folder" not in command
    assert command.count("--format") == 1


def test_build_download_command_preserves_album_question_mark_as_one_argument():
    command, album_mode = build_download_command(
        ["sldl"], "u", "p", "::ALBUM:: Steve Lacy - Oh yeah?",
        "/downloads", "/music", {}, 10,
    )

    assert album_mode is True
    assert command[command.index("--input") + 1] == "Steve Lacy - Oh yeah?"


def test_build_download_command_adds_exact_album_track_count_once():
    command, album_mode = build_download_command(
        ["sldl"], "u", "p", "::ALBUM:: Artist - Album",
        "/downloads", "/music", {}, 12,
    )

    assert album_mode is True
    assert command.count("--album-track-count") == 1
    flag_index = command.index("--album-track-count")
    assert command[flag_index + 1] == "12"

    preconfigured, _ = build_download_command(
        ["sldl", "--album-track-count", "12"],
        "u", "p", "::ALBUM:: Artist - Album",
        "/downloads", "/music", {}, 12,
    )
    assert preconfigured.count("--album-track-count") == 1


def test_build_download_command_rejects_conflicting_album_track_count():
    with pytest.raises(ValueError, match="conflicts"):
        build_download_command(
            ["sldl", "--album-track-count", "9"],
            "u", "p", "::ALBUM:: Artist - Album",
            "/downloads", "/music", {}, 12,
        )


def test_parse_download_query_removes_only_the_leading_album_marker():
    assert parse_download_query(
        "::ALBUM:: Artist - Album ::ALBUM:: Edition"
    ) == ("Artist - Album ::ALBUM:: Edition", True)


def test_build_download_command_always_hard_filters_album_to_flac():
    for strict_quality in (False, True):
        command, album_mode = build_download_command(
            ["sldl"], "u", "p", "::ALBUM:: Artist - Album",
            "/downloads", "/music", {"strict_quality": strict_quality},
        )

        assert album_mode is True
        format_index = command.index("--format")
        assert command[format_index + 1] == "flac"
        assert command.count("--format") == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "::ALBUM:: A$AP Rocky feat. Frank Ocean - TESTING",
            "::ALBUM:: A$AP Rocky - TESTING",
        ),
        (
            "::ALBUM:: Artist FT Guest - Album - Deluxe",
            "::ALBUM:: Artist - Album - Deluxe",
        ),
        (
            "::ALBUM:: Artist featuring Guest - Album",
            "::ALBUM:: Artist - Album",
        ),
    ],
)
def test_primary_artist_album_fallback_query_is_deterministic(query, expected):
    assert primary_artist_album_fallback_query(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "A$AP Rocky feat. Frank Ocean - TESTING",
        "::ALBUM:: Artist & Guest - Album",
        "::ALBUM:: Artist, Guest - Album",
        "::ALBUM:: Artist feat. - Album",
        "::ALBUM:: feat. Guest - Album",
        "::ALBUM:: Artist - Album",
        "::ALBUM::::ALBUM:: Artist feat. Guest - Album",
        "::ALBUM:: Artist feat. Guest - Album\n--desperate",
        "::ALBUM:: Artist feat.\tGuest - Album",
        "::ALBUM:: --help feat. Guest - Album",
        "::ALBUM:: Artist feat. Guest - --help",
        "::ALBUM:: Artist\x00 feat. Guest - Album",
    ],
)
def test_primary_artist_album_fallback_query_rejects_ambiguous_inputs(query):
    assert primary_artist_album_fallback_query(query) is None


def test_download_file_discovery_and_empty_cleanup(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "song.FLAC").write_bytes(b"audio")
    (nested / "cover.jpg").write_bytes(b"image")

    assert discover_downloaded_audio(str(tmp_path)) == [
        str(nested / "song.FLAC")
    ]

    (nested / "song.FLAC").unlink()
    (nested / "cover.jpg").unlink()
    cleanup_empty_download_directories(str(tmp_path))
    assert not nested.exists()


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


@patch("subprocess.Popen")
def test_zero_file_credited_album_retries_with_primary_artist_only(
    mock_popen,
    downloader,
    temp_dirs,
):
    first_process = MagicMock(stdout=[])
    first_process.returncode = 0
    second_process = MagicMock(stdout=[])
    second_process.returncode = 0
    staged_file = os.path.join(temp_dirs["downloads"], "01 result.flac")

    def finish_fallback_with_audio(timeout):
        with open(staged_file, "wb") as handle:
            handle.write(b"candidate")

    second_process.wait.side_effect = finish_fallback_with_audio
    mock_popen.side_effect = [first_process, second_process]
    callback = MagicMock()
    item = DownloadItem(
        id=81,
        search_query="::ALBUM:: A$AP Rocky feat. Frank Ocean - TESTING",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        callback,
        staging_dir=temp_dirs["downloads"],
    ) is True

    assert mock_popen.call_count == 2
    first_command = mock_popen.call_args_list[0].args[0]
    fallback_command = mock_popen.call_args_list[1].args[0]
    assert first_command[first_command.index("--input") + 1] == (
        "A$AP Rocky feat. Frank Ocean - TESTING"
    )
    assert fallback_command[fallback_command.index("--input") + 1] == (
        "A$AP Rocky - TESTING"
    )
    for command in (first_command, fallback_command):
        assert "--album" in command
        assert command[command.index("--format") + 1] == "flac"

    # Search broadening is command-local: canonical queue evidence is intact.
    assert item.search_query == (
        "::ALBUM:: A$AP Rocky feat. Frank Ocean - TESTING"
    )
    assert item.playlist_id == "canonical-playlist"
    assert item.mbid_guess == "canonical-release-mbid"
    callback.assert_any_call(
        "No album audio found; retrying with primary artist: "
        "A$AP Rocky - TESTING"
    )


@patch("subprocess.Popen")
def test_two_clean_zero_audio_album_results_report_failure(
    mock_popen,
    downloader,
    temp_dirs,
):
    first_process = MagicMock(stdout=[])
    first_process.returncode = 0
    second_process = MagicMock(stdout=[])
    second_process.returncode = 0
    mock_popen.side_effect = [first_process, second_process]
    item = DownloadItem(
        id=84,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        staging_dir=temp_dirs["downloads"],
    ) is False

    assert mock_popen.call_count == 2


@patch("subprocess.Popen")
def test_partial_credited_album_never_runs_primary_artist_retry(
    mock_popen,
    downloader,
    temp_dirs,
):
    process = MagicMock(stdout=[])
    process.returncode = 0
    staged_file = os.path.join(temp_dirs["downloads"], "01 partial.flac")

    def finish_with_partial_album(timeout):
        with open(staged_file, "wb") as handle:
            handle.write(b"partial")

    process.wait.side_effect = finish_with_partial_album
    mock_popen.return_value = process
    item = DownloadItem(
        id=82,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        staging_dir=temp_dirs["downloads"],
    ) is True

    mock_popen.assert_called_once()


@patch("subprocess.Popen")
def test_incomplete_audio_artifact_never_runs_primary_artist_retry(
    mock_popen,
    downloader,
    temp_dirs,
):
    process = MagicMock(stdout=[])
    process.returncode = 0
    staged_file = os.path.join(
        temp_dirs["downloads"],
        "01 partial.flac.incomplete",
    )

    def finish_with_incomplete_audio(timeout):
        with open(staged_file, "wb") as handle:
            handle.write(b"partial")

    process.wait.side_effect = finish_with_incomplete_audio
    mock_popen.return_value = process
    item = DownloadItem(
        id=86,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        staging_dir=temp_dirs["downloads"],
    ) is False
    mock_popen.assert_called_once()


def test_complete_plus_incomplete_album_artifact_preserves_failed_queue(
    downloader,
    db,
    temp_dirs,
):
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    ))
    item = db.get_downloads(status="pending")[0]
    staging_dir = tempfile.mkdtemp(dir=temp_dirs["downloads"])
    complete = os.path.join(staging_dir, "01 Complete.flac")
    incomplete = os.path.join(staging_dir, "02 Partial.flac.incomplete")
    for path, content in ((complete, b"complete"), (incomplete, b"partial")):
        with open(path, "wb") as handle:
            handle.write(content)

    def process(file_path, _item, _album_mode):
        os.remove(file_path)
        return ProcessedDownload("/music/01 Complete.flac", True)

    with patch.object(
        downloader,
        "_process_downloaded_file",
        side_effect=process,
    ):
        result = downloader._process_success(item, staging_dir=staging_dir)

    assert result == ProcessedQueueItem(
        changed_file_count=1,
        completed=False,
    )
    assert db.get_downloads(status="pending") == []
    assert [failed.id for failed in db.get_downloads(status="failed")] == [item.id]
    assert os.path.exists(incomplete)
    assert not os.path.exists(complete)


@pytest.mark.parametrize("configured_value", [False, "false", 0, None])
@patch("subprocess.Popen")
def test_primary_artist_album_retry_requires_true_boolean_config(
    mock_popen,
    configured_value,
    db,
    mock_scanner,
    temp_dirs,
):
    process = MagicMock(stdout=[])
    process.returncode = 0
    mock_popen.return_value = process
    downloader = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config={
            "slsk_album_primary_artist_fallback": configured_value,
        },
    )
    item = DownloadItem(
        id=85,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        staging_dir=temp_dirs["downloads"],
    ) is False
    mock_popen.assert_called_once()


@patch("subprocess.Popen")
def test_sldl_command_uses_argv_and_redacts_password_from_logs(
    mock_popen,
    downloader,
    caplog,
):
    process = MagicMock(stdout=[
        "connected using top-secret-password and inline-base-secret\n",
    ])
    process.returncode = 0
    mock_popen.return_value = process
    command = [
        "sldl",
        "--pass=inline-base-secret",
        "--pass",
        "base-command-secret",
        "--user",
        "u",
        "--pass",
        "top-secret-password",
        "--input",
        "Album\nforged-log-line; touch /tmp/not-executed",
    ]
    caplog.set_level("DEBUG", logger="src.downloader")

    callback = MagicMock()
    downloader._run_sldl_command(command, callback)

    called_command = mock_popen.call_args.args[0]
    assert called_command == command
    assert "shell" not in mock_popen.call_args.kwargs
    assert "top-secret-password" not in caplog.text
    assert "base-command-secret" not in caplog.text
    assert "inline-base-secret" not in caplog.text
    assert "--pass=<redacted>" in caplog.text
    assert "<redacted>" in caplog.text
    assert "Album\\nforged-log-line" in caplog.text
    callback.assert_called_once_with(
        "connected using <redacted> and <redacted>"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal workaround")
@patch("subprocess.Popen")
def test_sldl_command_receives_private_tty_stdin(mock_popen, downloader):
    process = MagicMock(stdout=[], returncode=0)
    observed = {}

    def launch(_command, **kwargs):
        observed["stdin_is_tty"] = os.isatty(kwargs["stdin"])
        return process

    mock_popen.side_effect = launch

    downloader._run_sldl_command(["sldl", "--input", "Artist - Album"])

    assert observed == {"stdin_is_tty": True}


@pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal workaround")
def test_sldl_real_child_observes_tty_stdin(downloader):
    output = []

    downloader._run_sldl_command(
        [sys.executable, "-c", "import os; print(os.isatty(0))"],
        output.append,
    )

    assert output == ["True"]


@patch("subprocess.Popen")
def test_sldl_failure_retains_bounded_redacted_output_tail(
    mock_popen,
    downloader,
):
    process = MagicMock(
        stdout=(
            ["oldest-sentinel\n"]
            + [f"diagnostic-{index}\n" for index in range(100)]
            + ["InvalidOperationException top-secret-password\n"]
            + ["at Program.<Main>(String[] args)\n"]
        ),
        returncode=1,
    )
    mock_popen.return_value = process

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        downloader._run_sldl_command(
            ["sldl", "--pass", "top-secret-password", "--input", "Album"]
        )

    assert "oldest-sentinel" not in exc_info.value.stdout
    assert "diagnostic-99" in exc_info.value.stdout
    assert "InvalidOperationException <redacted>" in exc_info.value.stdout
    assert "at Program.<Main>(String[] args)" in exc_info.value.stdout
    assert "top-secret-password" not in exc_info.value.stdout


@patch("subprocess.Popen")
def test_sldl_password_is_redacted_before_album_progress_persistence(
    mock_popen,
    downloader,
    db,
):
    process = MagicMock(stdout=["connected with top-secret-password\n"])
    process.returncode = 0
    mock_popen.return_value = process
    item = DownloadItem(
        id=db.queue_download(DownloadItem(
            search_query="::ALBUM:: Artist - Album",
            playlist_id="SATELLITE_ALBUM",
            mbid_guess="95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        )),
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess="95fb59ed-1ece-419b-b62f-aef31e0ebf36",
    )
    request_id = db.create_album_download_request(
        queue_item_id=item.id,
        release_mbid=item.mbid_guess,
        artist="Artist",
        title="Album",
        track_count=1,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS[:1],
    )

    downloader._run_sldl_command(
        ["sldl", "--pass", "top-secret-password"],
        lambda line: downloader._update_album_request_progress(
            item,
            "downloading",
            line,
        ),
    )

    detail = db.get_album_download_request(request_id)["detail"]
    assert detail == "connected with <redacted>"
    assert "top-secret-password" not in detail


@patch("subprocess.Popen")
def test_run_queue_preserves_zero_audio_item_as_failed(
    mock_popen,
    downloader,
    db,
):
    mock_popen.side_effect = [
        MagicMock(stdout=[], returncode=0),
        MagicMock(stdout=[], returncode=0),
    ]
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    ))

    downloader.run_queue()

    failed = db.get_downloads(status="failed")
    assert len(failed) == 1
    assert failed[0].search_query == (
        "::ALBUM:: Artist feat. Guest - Album"
    )
    assert failed[0].playlist_id == "canonical-playlist"
    assert failed[0].mbid_guess == "canonical-release-mbid"
    assert mock_popen.call_count == 2


def test_satellite_album_launch_uses_persisted_exact_track_count(
    downloader,
    db,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS,
    )
    item = db.get_downloads(status="pending")[0]

    def complete_with_audio(*_args, **_kwargs):
        with open(
            os.path.join(temp_dirs["downloads"], "01.flac"),
            "wb",
        ) as handle:
            handle.write(b"flac")

    with patch.object(
        downloader,
        "_run_sldl_command",
        side_effect=complete_with_audio,
    ) as run:
        assert downloader._attempt_download(
            item,
            staging_dir=temp_dirs["downloads"],
        ) is True

    command = run.call_args.args[0]
    assert command.count("--album-track-count") == 1
    assert command[command.index("--album-track-count") + 1] == "2"


def test_missing_downloader_binary_marks_satellite_tracker_failed(
    downloader,
    db,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS,
    )

    with patch.object(
        downloader,
        "_attempt_download",
        side_effect=FileNotFoundError("missing executable"),
    ):
        downloader.run_queue()

    assert db.get_download_status(queue_id) == "failed"
    tracker = db.get_album_download_request(request_id)
    assert tracker["stage"] == "failed"
    assert "command not found" in tracker["detail"]


@patch("subprocess.Popen")
def test_failed_credited_album_command_does_not_masquerade_as_zero_result(
    mock_popen,
    downloader,
    temp_dirs,
):
    process = MagicMock(stdout=[])
    process.returncode = 1
    mock_popen.return_value = process
    item = DownloadItem(
        id=83,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        downloader._attempt_download(
            item,
            staging_dir=temp_dirs["downloads"],
        )

    mock_popen.assert_called_once()
    assert "test_pass" not in exc_info.value.cmd
    assert "<redacted>" in exc_info.value.cmd


@patch("subprocess.Popen")
def test_primary_artist_album_retry_can_be_disabled(
    mock_popen,
    db,
    mock_scanner,
    temp_dirs,
):
    process = MagicMock(stdout=[])
    process.returncode = 0
    mock_popen.return_value = process
    downloader = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config={"slsk_album_primary_artist_fallback": False},
    )
    item = DownloadItem(
        id=83,
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    )

    assert downloader._attempt_download(
        item,
        staging_dir=temp_dirs["downloads"],
    ) is False

    mock_popen.assert_called_once()


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

    db.queue_download(DownloadItem(
        search_query="test song",
        playlist_id="test_playlist",
        mbid_guess="test_mbid"
    ))
    item = db.get_downloads(status="pending")[0]

    # Process success
    result = downloader._process_success(item)

    assert result == ProcessedQueueItem(
        changed_file_count=0,
        completed=False,
    )
    assert db.get_downloads(status="pending") == []
    assert len(db.get_downloads(status="failed")) == 1


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


def test_process_success_uses_shared_ingest_and_stamps_tag_tier(
    db, temp_dirs
):
    class Scanner:
        def process_file(self, path):
            if db.get_track_by_path(path):
                return "skipped"
            db.add_or_update_track(Track(
                mbid="download-mbid",
                title="Scanner Title",
                artist="Scanner Artist",
                album="Scanner Album",
                local_path=path,
            ))
            return "processed"

    dl = Downloader(
        db=db,
        scanner=Scanner(),
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
    )
    src = os.path.join(temp_dirs["downloads"], "raw.flac")
    with open(src, "wb") as handle:
        handle.write(b"audio")
    dl._auto_tag_file = MagicMock(return_value=(
        {
            "artist": "Sort Artist",
            "album": "Sort Album",
            "title": "Sort Title",
            "track_number": 3,
        },
        "green",
        0.99,
    ))
    item = DownloadItem(
        id=123,
        search_query="Artist - Song",
        playlist_id="playlist",
        mbid_guess="",
    )

    with patch("mutagen.File", return_value=MagicMock()):
        result = dl._process_success(item)

    track = db.get_track_by_mbid("download-mbid")
    assert track.local_path.endswith("Sort Artist/Sort Album/03 Sort Title.flac")
    assert track.tag_tier == "green"
    assert track.tag_score == 0.99
    assert result == ProcessedQueueItem(
        changed_file_count=1,
        completed=True,
    )


def test_process_success_counts_only_completed_imports(db, mock_scanner, temp_dirs):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
    )
    item = DownloadItem(
        id=123,
        search_query="::ALBUM:: Artist - Album",
        playlist_id="playlist",
        mbid_guess="",
    )

    with patch(
        "src.downloader.discover_downloaded_audio",
        return_value=["one.mp3", "two.mp3", "three.mp3"],
    ), patch.object(
        dl,
        "_process_downloaded_file",
        side_effect=["/music/one.mp3", None, RuntimeError("bad tags")],
    ):
        result = dl._process_success(item)

    assert result == ProcessedQueueItem(
        changed_file_count=1,
        completed=False,
    )


def test_rejected_album_audio_preserves_queue_identity_as_failed(
    downloader,
    db,
):
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist feat. Guest - Album",
        playlist_id="canonical-playlist",
        mbid_guess="canonical-release-mbid",
    ))
    item = db.get_downloads(status="pending")[0]

    with patch(
        "src.downloader.discover_downloaded_audio",
        return_value=["accepted.flac", "rejected.mp3"],
    ), patch.object(
        downloader,
        "_process_downloaded_file",
        side_effect=[
            ProcessedDownload("/music/accepted.flac", True),
            None,
        ],
    ):
        result = downloader._process_success(item)

    assert result == ProcessedQueueItem(
        changed_file_count=1,
        completed=False,
    )
    failed = db.get_downloads(status="failed")
    assert len(failed) == 1
    assert failed[0].search_query == item.search_query
    assert failed[0].playlist_id == item.playlist_id
    assert failed[0].mbid_guess == item.mbid_guess


def test_album_processing_never_imports_non_flac(
    db, mock_scanner, temp_dirs
):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
    )
    item = DownloadItem(
        id=123,
        search_query="::ALBUM:: Artist - Album",
        playlist_id="playlist",
        mbid_guess="",
    )

    with patch.object(dl, "_auto_tag_file") as auto_tag, patch.object(
        dl, "_scan_downloaded_file"
    ) as scan, patch(
        "src.downloader.ingest_downloaded_audio_file_with_result"
    ) as ingest:
        result = dl._process_downloaded_file(
            os.path.join(temp_dirs["downloads"], "lossy.mp3"),
            item,
            is_album_mode=True,
        )

    assert result is None
    auto_tag.assert_not_called()
    scan.assert_not_called()
    ingest.assert_not_called()


@pytest.mark.parametrize(
    ("is_album_mode", "search_query", "mbid_guess"),
    [
        (False, "Artist - Track", ""),
        (
            True,
            "::ALBUM:: Artist - Album",
            "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        ),
    ],
)
def test_every_new_soulseek_queue_type_runs_picard_stage(
    is_album_mode,
    search_query,
    mbid_guess,
    downloader,
):
    item = DownloadItem(
        id=122,
        search_query=search_query,
        playlist_id="ordinary-queue",
        mbid_guess=mbid_guess,
    )
    metadata = MagicMock(
        artist="Artist",
        album="Album",
        title="Track",
        track_number=1,
        disc_number=1,
    )
    ingest_result = MagicMock(path="/music/canonical.flac", changed=True)
    with patch(
        "src.downloader.normalize_embedded_recording_mbid",
        return_value=None,
    ), patch.object(
        downloader,
        "_auto_tag_file",
        return_value=(None, None, None),
    ) as auto_tag, patch.object(
        downloader,
        "_scan_downloaded_file",
    ), patch(
        "src.downloader.read_embedded_recording_mbid",
        return_value=None,
    ), patch(
        "src.downloader.read_downloaded_metadata",
        return_value=metadata,
    ), patch(
        "src.downloader.ingest_downloaded_audio_file_with_result",
        return_value=ingest_result,
    ):
        result = downloader._process_downloaded_file(
            "/downloads/new.flac",
            item,
            is_album_mode=is_album_mode,
        )

    assert result == ProcessedDownload("/music/canonical.flac", True)
    auto_tag.assert_called_once_with(
        "/downloads/new.flac",
        expected_release_mbid="",
        expected_recording_mbid="",
    )


def test_album_processing_passes_embedded_recording_mbid_not_release_mbid(
    db, mock_scanner, temp_dirs
):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
    )
    item = DownloadItem(
        id=123,
        search_query="::ALBUM:: Artist - Album",
        playlist_id="playlist",
        mbid_guess="release-mbid-must-not-be-a-recording",
    )
    staged = os.path.join(temp_dirs["downloads"], "04 Track.flac")
    metadata = MagicMock(
        artist="Artist",
        album="Album",
        title="Track",
        track_number=4,
    )
    ingest_result = MagicMock(
        path=os.path.join(temp_dirs["music_library"], "canonical.flac"),
        changed=False,
    )
    events = []

    with patch.object(
        dl,
        "_auto_tag_file",
        return_value=(None, None, None),
    ), patch.object(
        dl,
        "_scan_downloaded_file",
        side_effect=lambda _path: events.append("scan"),
    ), patch(
        "src.downloader.read_embedded_recording_mbid",
        side_effect=lambda _path: events.append("recording") or "recording-mbid",
    ) as read_mbid, patch(
        "src.downloader.read_downloaded_metadata",
        return_value=metadata,
    ), patch(
        "src.downloader.ingest_downloaded_audio_file_with_result",
        return_value=ingest_result,
    ) as ingest:
        result = dl._process_downloaded_file(staged, item, is_album_mode=True)

    assert events == ["scan", "recording"]
    read_mbid.assert_called_once_with(staged)
    assert ingest.call_args.kwargs["recording_mbid"] == "recording-mbid"
    assert "release-mbid" not in repr(ingest.call_args.kwargs)
    assert result == ProcessedDownload(ingest_result.path, False)


def test_single_track_canonicalizes_uppercase_queue_mbid_before_scan(
    db, mock_scanner, temp_dirs
):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
    )
    canonical = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
    item = DownloadItem(
        id=124,
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess=canonical.upper(),
    )
    staged = os.path.join(temp_dirs["downloads"], "Track.flac")
    metadata = MagicMock(
        artist="Artist",
        album="Album",
        title="Track",
        track_number=1,
    )
    ingest_result = MagicMock(
        path=os.path.join(temp_dirs["music_library"], "canonical.flac"),
        changed=True,
    )
    events = []

    with patch.object(
        dl,
        "_auto_tag_file",
        return_value=({"mbid": canonical}, "green", 0.99),
    ), patch(
        "src.downloader.normalize_embedded_recording_mbid",
        side_effect=lambda _path: events.append("normalize") or canonical,
    ), patch.object(
        dl,
        "_scan_downloaded_file",
        side_effect=lambda _path: events.append("scan"),
    ), patch(
        "src.downloader.read_embedded_recording_mbid",
        return_value=canonical,
    ), patch(
        "src.downloader.read_downloaded_metadata",
        return_value=metadata,
    ), patch(
        "src.downloader.ingest_downloaded_audio_file_with_result",
        return_value=ingest_result,
    ):
        result = dl._process_downloaded_file(staged, item, is_album_mode=False)

    assert events == ["normalize", "normalize", "scan"]
    assert result == ProcessedDownload(ingest_result.path, True)


def test_single_track_rejects_non_uuid_queue_mbid_before_scan(
    downloader,
):
    item = DownloadItem(
        id=125,
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess="not-a-musicbrainz-uuid",
    )

    with patch(
        "src.downloader.normalize_embedded_recording_mbid",
        return_value=None,
    ), patch.object(
        downloader,
        "_auto_tag_file",
        return_value=(None, None, None),
    ), patch.object(
        downloader,
        "_scan_downloaded_file",
    ) as scan:
        result = downloader._process_downloaded_file(
            "/downloads/Track.flac",
            item,
            is_album_mode=False,
        )

    assert result is None
    scan.assert_not_called()


def test_single_track_rejects_conflicting_embedded_recording_before_lookup(
    downloader,
):
    requested = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
    embedded = "00000000-0000-4000-8000-000000000002"
    item = DownloadItem(
        id=126,
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess=requested,
    )

    with patch(
        "src.downloader.normalize_embedded_recording_mbid",
        return_value=embedded,
    ), patch.object(downloader, "_auto_tag_file") as auto_tag, patch.object(
        downloader,
        "_scan_downloaded_file",
    ) as scan:
        result = downloader._process_downloaded_file(
            "/downloads/Track.flac",
            item,
            is_album_mode=False,
        )

    assert result is None
    auto_tag.assert_not_called()
    scan.assert_not_called()


@pytest.mark.parametrize("tier", [None, "yellow", "red"])
def test_identity_bound_single_track_requires_green_acoustic_result(
    tier,
    downloader,
):
    requested = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
    item = DownloadItem(
        id=127,
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess=requested,
    )

    with patch(
        "src.downloader.normalize_embedded_recording_mbid",
        return_value=None,
    ), patch.object(
        downloader,
        "_auto_tag_file",
        return_value=(None, tier, 0.7 if tier else None),
    ) as auto_tag, patch.object(
        downloader,
        "_scan_downloaded_file",
    ) as scan:
        result = downloader._process_downloaded_file(
            "/downloads/Track.flac",
            item,
            is_album_mode=False,
        )

    assert result is None
    auto_tag.assert_called_once_with(
        "/downloads/Track.flac",
        expected_release_mbid="",
        expected_recording_mbid=requested,
    )
    scan.assert_not_called()


def test_identity_bound_single_track_requires_persisted_verified_recording(
    downloader,
):
    requested = "09544ff9-57c8-48d6-a4d7-e2ea43478f59"
    item = DownloadItem(
        id=128,
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess=requested,
    )

    with patch(
        "src.downloader.normalize_embedded_recording_mbid",
        return_value=None,
    ), patch.object(
        downloader,
        "_auto_tag_file",
        return_value=({"mbid": requested}, "green", 0.99),
    ), patch(
        "src.downloader.read_embedded_recording_mbid",
        return_value=None,
    ), patch.object(
        downloader,
        "_scan_downloaded_file",
    ) as scan:
        result = downloader._process_downloaded_file(
            "/downloads/Track.flac",
            item,
            is_album_mode=False,
        )

    assert result is None
    scan.assert_not_called()


def test_run_queue_refreshes_jellyfin_once_after_local_imports(
    downloader, db
):
    db.queue_download(DownloadItem(
        search_query="Artist - First",
        playlist_id="playlist",
        mbid_guess="",
    ))
    db.queue_download(DownloadItem(
        search_query="Artist - Second",
        playlist_id="playlist",
        mbid_guess="",
    ))
    jellyfin = MagicMock()
    downloader.jellyfin_client = jellyfin
    events = []

    with patch.object(downloader, "_attempt_download", return_value=True), \
         patch.object(
             downloader,
             "_process_success",
             side_effect=lambda *_args, **_kwargs: events.append("import") or 1,
         ):
        jellyfin.trigger_library_scan.side_effect = lambda: events.append("refresh")
        downloader.run_queue()

    assert events == ["import", "import", "refresh"]
    jellyfin.trigger_library_scan.assert_called_once_with()


def test_run_queue_uses_fresh_staging_directory_for_each_item(
    downloader, db, temp_dirs
):
    for query in ("Artist - First", "Artist - Second"):
        db.queue_download(DownloadItem(
            search_query=query,
            playlist_id="playlist",
            mbid_guess="",
        ))
    stale = os.path.join(temp_dirs["downloads"], "unrelated.flac")
    with open(stale, "wb") as handle:
        handle.write(b"unrelated")

    staging_dirs = []
    processed_files = []
    jellyfin = MagicMock()
    downloader.jellyfin_client = jellyfin

    def attempt(item, _callback=None, *, staging_dir=None):
        assert staging_dir is not None
        staging_dirs.append(staging_dir)
        with open(os.path.join(staging_dir, f"{item.id}.flac"), "wb") as handle:
            handle.write(b"candidate")
        return True

    def process(file_path, _item, _album_mode):
        processed_files.append(file_path)
        os.remove(file_path)
        return ProcessedDownload(
            path=os.path.join(temp_dirs["music_library"], os.path.basename(file_path)),
            library_changed=True,
        )

    with patch.object(downloader, "_attempt_download", side_effect=attempt), \
         patch.object(downloader, "_process_downloaded_file", side_effect=process):
        downloader.run_queue()

    assert len(staging_dirs) == 2
    assert staging_dirs[0] != staging_dirs[1]
    assert all(os.path.dirname(path) == temp_dirs["downloads"] for path in staging_dirs)
    assert all(not os.path.exists(path) for path in staging_dirs)
    assert {os.path.basename(path) for path in processed_files} == {"1.flac", "2.flac"}
    assert os.path.exists(stale)
    jellyfin.trigger_library_scan.assert_called_once_with()


@pytest.mark.parametrize(
    ("library_changed", "refresh_expected"),
    [(False, False), (True, True)],
    ids=("equal-or-better-existing", "strict-quality-upgrade"),
)
def test_satisfied_collision_removes_queue_and_refreshes_only_for_media_change(
    downloader,
    db,
    temp_dirs,
    library_changed,
    refresh_expected,
):
    db.queue_download(DownloadItem(
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess="track-mbid",
    ))
    jellyfin = MagicMock()
    downloader.jellyfin_client = jellyfin

    def attempt(_item, _callback=None, *, staging_dir=None):
        with open(os.path.join(staging_dir, "candidate.flac"), "wb") as handle:
            handle.write(b"candidate")
        return True

    def process(file_path, _item, _album_mode):
        os.remove(file_path)
        return ProcessedDownload(
            path=os.path.join(temp_dirs["music_library"], "Artist", "Track.flac"),
            library_changed=library_changed,
        )

    with patch.object(downloader, "_attempt_download", side_effect=attempt), \
         patch.object(downloader, "_process_downloaded_file", side_effect=process):
        downloader.run_queue()

    assert db.get_downloads(status="pending") == []
    assert db.get_downloads(status="failed") == []
    if refresh_expected:
        jellyfin.trigger_library_scan.assert_called_once_with()
    else:
        jellyfin.trigger_library_scan.assert_not_called()


def test_satellite_album_request_tracks_download_import_and_verified_success(
    downloader,
    db,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS,
    )

    def attempt(_item, callback=None, *, staging_dir=None):
        if callback:
            callback("Downloading lossless files 50%")
        for number in (1, 2):
            with open(
                os.path.join(staging_dir, f"{number:02d}.flac"),
                "wb",
            ) as handle:
                handle.write(b"flac")
        return True

    def process(file_path, _item, _album_mode, _manifest=None):
        number = int(os.path.basename(file_path).split(".")[0])
        os.remove(file_path)
        target = os.path.join(temp_dirs["music_library"], f"{number:02d}.flac")
        _write_tagged_flac(
            target,
            SATELLITE_RECORDINGS[number - 1],
            release_mbid,
        )
        db.add_or_update_track(Track(
            mbid=SATELLITE_RECORDINGS[number - 1],
            title=f"Track {number}",
            artist="Artist",
            album="Album",
            release_mbid=release_mbid,
            local_path=target,
        ))
        return ProcessedDownload(path=target, library_changed=True)

    with patch.object(downloader, "_attempt_download", side_effect=attempt), \
         patch.object(
             downloader,
             "_process_downloaded_file",
             side_effect=process,
         ):
        downloader.run_queue()

    tracker = db.get_album_download_request(request_id)
    assert tracker["stage"] == "success"
    assert tracker["completed_tracks"] == 2
    assert "2 FLAC" in tracker["detail"]
    assert db.get_download_status(queue_id) is None


def test_satellite_album_request_fails_closed_when_musicbrainz_set_incomplete(
    downloader,
    db,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS,
    )

    def attempt(_item, _callback=None, *, staging_dir=None):
        with open(os.path.join(staging_dir, "01.flac"), "wb") as handle:
            handle.write(b"flac")
        return True

    def process(file_path, _item, _album_mode, _manifest=None):
        os.remove(file_path)
        target = os.path.join(temp_dirs["music_library"], "01.flac")
        _write_tagged_flac(target, SATELLITE_RECORDINGS[0], release_mbid)
        db.add_or_update_track(Track(
            mbid=SATELLITE_RECORDINGS[0],
            title="Track 1",
            artist="Artist",
            album="Album",
            release_mbid=release_mbid,
            local_path=target,
        ))
        return ProcessedDownload(path=target, library_changed=True)

    with patch.object(downloader, "_attempt_download", side_effect=attempt), \
         patch.object(
             downloader,
             "_process_downloaded_file",
             side_effect=process,
         ):
        downloader.run_queue()

    tracker = db.get_album_download_request(request_id)
    assert tracker["stage"] == "failed"
    assert "1 of 2" in tracker["detail"]
    assert db.get_download_status(queue_id) == "failed"


def test_satellite_album_rejects_wrong_recording_before_import_or_mirror(
    downloader,
    db,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=1,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS[:1],
    )
    staged = os.path.join(temp_dirs["downloads"], "wrong.flac")
    _write_tagged_flac(staged, SATELLITE_RECORDINGS[1], release_mbid)
    item = db.get_downloads(status="pending")[0]

    with patch.object(downloader, "_mirror_to_jellyfin") as mirror:
        result = downloader._process_success(item, temp_dirs["downloads"])

    assert result.completed is False
    assert db.get_track_by_mbid(SATELLITE_RECORDINGS[1]) is None
    assert db.get_album_download_request(request_id)["stage"] == "failed"
    assert db.get_download_status(queue_id) == "failed"
    assert os.path.isfile(staged)
    mirror.assert_not_called()


def test_satellite_album_preserves_recording_owned_by_another_live_release(
    downloader,
    db,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    other_release = "461eac33-7edd-481a-a7d1-089ec6fc01af"
    existing_path = os.path.join(temp_dirs["music_library"], "existing.flac")
    _write_tagged_flac(
        existing_path,
        SATELLITE_RECORDINGS[0],
        other_release,
    )
    db.add_or_update_track(Track(
        mbid=SATELLITE_RECORDINGS[0],
        title="Existing Edition",
        artist="Artist",
        album="Other Album",
        release_mbid=other_release,
        local_path=existing_path,
    ))
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=1,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS[:1],
    )
    staged = os.path.join(temp_dirs["downloads"], "same-recording.flac")
    _write_tagged_flac(staged, SATELLITE_RECORDINGS[0], release_mbid)
    item = db.get_downloads(status="pending")[0]

    with patch.object(downloader, "_mirror_to_jellyfin") as mirror:
        result = downloader._process_success(item, temp_dirs["downloads"])

    assert result.completed is False
    preserved = db.get_track_by_mbid(SATELLITE_RECORDINGS[0])
    assert preserved.release_mbid == other_release
    assert preserved.local_path == existing_path
    assert db.get_album_download_request(request_id)["stage"] == "failed"
    mirror.assert_not_called()


def test_satellite_failed_album_row_is_marked_active_before_automatic_retry(
    downloader,
    db,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    queue_id = db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=release_mbid,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="failed",
        detail="Earlier failure",
        completed_tracks=0,
        recording_mbids=SATELLITE_RECORDINGS,
    )
    db.update_download_status(queue_id, "failed")
    observed = {}

    def attempt(_item, _callback=None, *, staging_dir=None):
        observed["queue_status"] = db.get_download_status(queue_id)
        observed["stage"] = db.get_album_download_request(request_id)["stage"]
        return False

    with patch.object(downloader, "_attempt_download", side_effect=attempt):
        downloader.run_queue()

    assert observed == {"queue_status": "pending", "stage": "downloading"}
    assert db.get_download_status(queue_id) == "failed"
    assert db.get_album_download_request(request_id)["stage"] == "failed"


def test_run_queue_mirrors_each_import_before_single_jellyfin_refresh(
    db, mock_scanner, temp_dirs
):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        jellyfin_music_library_dir="/jellyfin-music",
    )
    db.queue_download(DownloadItem(
        search_query="Artist - Song",
        playlist_id="playlist",
        mbid_guess="",
    ))
    jellyfin = MagicMock()
    dl.jellyfin_client = jellyfin
    imported_path = os.path.join(
        temp_dirs["music_library"],
        "Artist",
        "Album",
        "01 Song.flac",
    )
    events = []

    with patch.object(dl, "_attempt_download", return_value=True), patch(
        "src.downloader.discover_downloaded_audio",
        return_value=[os.path.join(temp_dirs["downloads"], "song.flac")],
    ), patch.object(
        dl,
        "_process_downloaded_file",
        side_effect=lambda *_args: events.append("import") or imported_path,
    ), patch(
        "src.downloader.mirror_imported_file",
        side_effect=lambda *_args: events.append("mirror") or "/mirror/song.flac",
    ):
        jellyfin.trigger_library_scan.side_effect = lambda: events.append("refresh")
        dl.run_queue()

    assert events == ["import", "mirror", "refresh"]
    jellyfin.trigger_library_scan.assert_called_once_with()
    assert db.get_downloads(status="pending") == []


def test_mirror_failure_does_not_undo_import_or_suppress_refresh(
    db, mock_scanner, temp_dirs, caplog
):
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        jellyfin_music_library_dir="/missing-mirror",
    )
    db.queue_download(DownloadItem(
        search_query="Artist - Song",
        playlist_id="playlist",
        mbid_guess="",
    ))
    jellyfin = MagicMock()
    dl.jellyfin_client = jellyfin
    imported_path = os.path.join(temp_dirs["music_library"], "song.flac")
    events = []

    def mirror_failure(*_args):
        events.append("mirror")
        raise OSError("mirror unavailable")

    with patch.object(dl, "_attempt_download", return_value=True), patch(
        "src.downloader.discover_downloaded_audio",
        return_value=[os.path.join(temp_dirs["downloads"], "song.flac")],
    ), patch.object(
        dl,
        "_process_downloaded_file",
        side_effect=lambda *_args: events.append("import") or imported_path,
    ), patch(
        "src.downloader.mirror_imported_file",
        side_effect=mirror_failure,
    ):
        jellyfin.trigger_library_scan.side_effect = lambda: events.append("refresh")
        dl.run_queue()

    assert events == ["import", "mirror", "refresh"]
    assert "mirror unavailable" in caplog.text
    assert db.get_downloads(status="pending") == []
    jellyfin.trigger_library_scan.assert_called_once_with()


def test_blank_jellyfin_mirror_path_disables_copy(downloader):
    downloader.jellyfin_music_library_dir = None

    with patch("src.downloader.mirror_imported_file") as mirror:
        downloader._mirror_to_jellyfin("/music/song.flac")

    mirror.assert_not_called()


def test_run_queue_skips_jellyfin_refresh_when_no_file_was_imported(
    downloader, db
):
    db.queue_download(DownloadItem(
        search_query="Artist - Existing Song",
        playlist_id="playlist",
        mbid_guess="",
    ))
    jellyfin = MagicMock()
    downloader.jellyfin_client = jellyfin

    with patch.object(downloader, "_attempt_download", return_value=True), \
         patch.object(downloader, "_process_success", return_value=0):
        downloader.run_queue()

    jellyfin.trigger_library_scan.assert_not_called()


def test_run_queue_skips_jellyfin_refresh_when_download_fails(
    downloader, db
):
    db.queue_download(DownloadItem(
        search_query="Artist - Failed Song",
        playlist_id="playlist",
        mbid_guess="",
    ))
    jellyfin = MagicMock()
    downloader.jellyfin_client = jellyfin

    with patch.object(
        downloader,
        "_attempt_download",
        side_effect=subprocess.CalledProcessError(
            1, ["sldl"], output="download failed"
        ),
    ):
        downloader.run_queue()

    jellyfin.trigger_library_scan.assert_not_called()
    assert len(db.get_downloads(status="failed")) == 1


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


def test_auto_tag_complete_picard_tags_are_still_acoustically_verified(
    db, temp_dirs
):
    dl = _downloader_with_key(db, temp_dirs)
    complete = {
        "title": "Track", "artist": "Artist", "album": "Album",
        "album_artist": "Artist", "track_number": "1", "track_total": "10",
        "disc_number": "1", "disc_total": "1",
        "mbid": "00000000-0000-4000-8000-000000000001",
        "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        "release_track_mbid": "10000000-0000-4000-8000-000000000001",
    }
    candidate = {"score": 0.98, "tier": "green", "meta": complete}
    with patch(
        "src.downloader.tag_service.read_current_tags",
        return_value=complete,
    ), patch(
        "src.downloader.tag_service.has_complete_picard_tags",
        return_value=True,
    ), patch(
        "src.downloader.tag_service.identify_file_for_release",
        return_value=candidate,
    ) as identify, patch(
        "src.downloader.tag_service.write_tags_atomic",
    ) as write_atomic:
        meta, tier, score = dl._auto_tag_file("/whatever.flac")
    assert tier == "green"
    assert meta == complete
    assert score == 0.98
    identify.assert_called_once_with(
        "/whatever.flac",
        "KEY",
        complete["release_mbid"],
        complete["mbid"],
        "me@x",
    )
    write_atomic.assert_called_once_with("/whatever.flac", complete)


def test_auto_tag_green_match_writes_tags(db, temp_dirs):
    dl = _downloader_with_key(db, temp_dirs)
    candidate = {
        "score": 0.97,
        "tier": "green",
        "meta": {
            "artist": "A", "album_artist": "A", "title": "T",
            "album": "Al", "date": "2026", "track_number": 1,
            "track_total": 10, "disc_number": 1, "disc_total": 1,
            "mbid": "00000000-0000-4000-8000-000000000001",
            "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
            "release_track_mbid": "10000000-0000-4000-8000-000000000001",
        },
    }
    with patch(
        "src.downloader.tag_service.has_complete_picard_tags",
        return_value=False,
    ), patch(
        "src.downloader.tag_service.identify_file",
        return_value=candidate,
    ), patch("src.downloader.tag_service.write_tags_atomic") as mock_write:
        meta, tier, score = dl._auto_tag_file("/some.flac")
    mock_write.assert_called_once_with("/some.flac", candidate["meta"])
    assert tier == "green"
    assert meta == candidate["meta"]
    assert score == 0.97


def test_auto_tag_green_match_cannot_override_requested_recording(
    db, temp_dirs
):
    dl = _downloader_with_key(db, temp_dirs)
    requested = "00000000-0000-4000-8000-000000000001"
    wrong_recording = "00000000-0000-4000-8000-000000000002"
    candidate = {
        "score": 0.99,
        "tier": "green",
        "meta": {
            "artist": "A",
            "album_artist": "A",
            "title": "Wrong Track",
            "album": "Album",
            "track_number": 1,
            "track_total": 1,
            "disc_number": 1,
            "disc_total": 1,
            "mbid": wrong_recording,
            "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
            "release_track_mbid": (
                "10000000-0000-4000-8000-000000000001"
            ),
        },
    }
    with patch(
        "src.downloader.tag_service.read_current_tags",
        return_value={},
    ), patch(
        "src.downloader.tag_service.has_complete_picard_tags",
        return_value=False,
    ), patch(
        "src.downloader.tag_service.identify_file",
        return_value=candidate,
    ), patch(
        "src.downloader.tag_service.write_tags_atomic",
    ) as write_atomic:
        result = dl._auto_tag_file(
            "/some.flac",
            expected_recording_mbid=requested,
        )

    assert result == (None, "red", 0.99)
    write_atomic.assert_not_called()


def test_auto_tag_write_failure_is_red_and_never_claims_green(db, temp_dirs):
    dl = _downloader_with_key(db, temp_dirs)
    candidate = {
        "score": 0.97,
        "tier": "green",
        "meta": {
            "artist": "A", "album_artist": "A", "title": "T",
            "album": "Al", "date": "2026", "track_number": 1,
            "track_total": 1, "disc_number": 1, "disc_total": 1,
            "mbid": SATELLITE_RECORDINGS[0],
            "release_mbid": "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
            "release_track_mbid": "10000000-0000-4000-8000-000000000001",
        },
    }
    with patch(
        "src.downloader.tag_service.has_complete_picard_tags",
        return_value=False,
    ), patch(
        "src.downloader.tag_service.identify_file",
        return_value=candidate,
    ), patch(
        "src.downloader.tag_service.write_tags_atomic",
        side_effect=OSError("write failed"),
    ):
        meta, tier, score = dl._auto_tag_file("/some.flac")

    assert meta is None
    assert tier == "red"
    assert score == 0.97


def _exact_album_manifest(release_mbid):
    return {
        "release_mbid": release_mbid,
        "artist": "Canonical Album Artist",
        "title": "Canonical Album",
        "recording_mbids": SATELLITE_RECORDINGS[:1],
        "tracks": ({
            "position": 1,
            "medium_position": 2,
            "track_position": 3,
            "track_number": "3",
            "recording_mbid": SATELLITE_RECORDINGS[0],
            "title": "Canonical Track",
            "artist": "Canonical Track Artist",
            "date": "2026-07-18",
            "track_total": 8,
            "disc_total": 2,
            "release_track_mbid": (
                "10000000-0000-4000-8000-000000000001"
            ),
        },),
    }


@pytest.mark.parametrize("embedded_recording", [True, False])
def test_exact_album_flow_acoustically_verifies_then_manifest_wins(
    embedded_recording,
    db,
    mock_scanner,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    staged = os.path.join(temp_dirs["downloads"], "03 Source.flac")
    _write_tagged_flac(staged, SATELLITE_RECORDINGS[0], release_mbid)
    if not embedded_recording:
        source = FLAC(staged)
        del source["musicbrainz_trackid"]
        source.save()
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config={"acoustid_api_key": "key", "contact_email": "me@x"},
    )
    candidate = {
        "score": 0.98,
        "tier": "green",
        "meta": {
            "artist": "AcoustID Artist", "album_artist": "AcoustID Artist",
            "title": "AcoustID Title", "album": "Same Selected Release",
            "date": "2026", "track_number": 3, "track_total": 8,
            "disc_number": 2, "disc_total": 2,
            "mbid": SATELLITE_RECORDINGS[0],
            "release_mbid": release_mbid,
            "release_track_mbid": (
                "10000000-0000-4000-8000-000000000001"
            ),
        },
    }
    item = DownloadItem(
        id=44,
        search_query="::ALBUM:: Canonical Album Artist - Canonical Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    )
    ingest_result = MagicMock(path=staged, changed=True)

    with patch(
        "src.downloader.tag_service.identify_file_for_release",
        return_value=candidate,
    ) as identify, patch.object(
        dl,
        "_scan_downloaded_file",
    ), patch(
        "src.downloader.ingest_downloaded_audio_file_with_result",
        return_value=ingest_result,
    ), patch(
        "src.downloader.tag_service.write_tags_atomic",
        wraps=tag_service.write_tags_atomic,
    ) as write_atomic:
        result = dl._process_downloaded_file(
            staged,
            item,
            is_album_mode=True,
            album_manifest=_exact_album_manifest(release_mbid),
        )

    assert result == ProcessedDownload(staged, True)
    identify.assert_called_once_with(
        staged,
        "key",
        release_mbid,
        SATELLITE_RECORDINGS[0] if embedded_recording else "",
        "me@x",
    )
    write_atomic.assert_called_once_with(
        staged,
        exact_manifest_tag_metadata(
            _exact_album_manifest(release_mbid),
            SATELLITE_RECORDINGS[0],
        ),
    )
    audio = FLAC(staged)
    assert audio["title"] == ["Canonical Track"]
    assert audio["artist"] == ["Canonical Track Artist"]
    assert audio["albumartist"] == ["Canonical Album Artist"]
    assert audio["tracknumber"] == ["3"]
    assert audio["tracktotal"] == ["8"]
    assert audio["discnumber"] == ["2"]
    assert audio["disctotal"] == ["2"]
    assert audio["musicbrainz_releasetrackid"] == [
        "10000000-0000-4000-8000-000000000001"
    ]


@pytest.mark.parametrize("tagging_config", [
    {"acoustid_api_key": ""},
    {"acoustid_api_key": "key", "auto_tag_downloads": False},
])
def test_exact_album_fails_closed_without_acoustic_verification(
    tagging_config,
    db,
    mock_scanner,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    staged = os.path.join(temp_dirs["downloads"], "source.flac")
    _write_tagged_flac(staged, SATELLITE_RECORDINGS[0], release_mbid)
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config=tagging_config,
    )
    item = DownloadItem(
        id=45,
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=release_mbid,
    )

    with patch.object(dl, "_scan_downloaded_file") as scan:
        result = dl._process_downloaded_file(
            staged,
            item,
            is_album_mode=True,
            album_manifest=_exact_album_manifest(release_mbid),
        )

    assert result is None
    scan.assert_not_called()


def test_exact_album_reverifies_even_complete_embedded_picard_tags(
    db,
    mock_scanner,
    temp_dirs,
):
    release_mbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    staged = os.path.join(temp_dirs["downloads"], "complete.flac")
    _write_tagged_flac(staged, SATELLITE_RECORDINGS[0], release_mbid)
    tag_service.write_tags(staged, {
        "title": "Canonical Track",
        "artist": "Canonical Track Artist",
        "album": "Canonical Album",
        "album_artist": "Canonical Album Artist",
        "date": "2026-07-18",
        "track_number": 3,
        "track_total": 8,
        "disc_number": 2,
        "disc_total": 2,
        "mbid": SATELLITE_RECORDINGS[0],
        "release_mbid": release_mbid,
        "release_track_mbid": "10000000-0000-4000-8000-000000000001",
    })
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["sldl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        slsk_config={"acoustid_api_key": "key"},
    )

    with patch(
        "src.downloader.tag_service.identify_file_for_release",
        return_value=None,
    ) as identify:
        meta, tier, score = dl._auto_tag_file(
            staged,
            expected_release_mbid=release_mbid,
        )

    identify.assert_called_once_with(
        staged,
        "key",
        release_mbid,
        SATELLITE_RECORDINGS[0],
        "",
    )
    assert (meta, tier, score) == (None, "red", None)


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


def test_auto_tag_can_be_disabled_for_new_non_manifest_downloads(db, temp_dirs):
    dl = Downloader(
        db=db, scanner=MagicMock(), slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u", slsk_password="p",
        slsk_config={"acoustid_api_key": "key", "auto_tag_downloads": False},
    )
    with patch(
        "src.downloader.tag_service.has_complete_picard_tags",
        return_value=False,
    ), patch("src.downloader.tag_service.identify_file") as identify:
        result = dl._auto_tag_file("/new-download.flac")

    assert result == (None, None, None)
    identify.assert_not_called()


def test_main_run_downloader(db, temp_dirs):
    """Test main downloader function."""
    config = {
        "slsk_cmd_base": ["slsk-batchdl"],
        "downloads_path": temp_dirs["downloads"],
        "music_library_path": temp_dirs["music_library"],
        "slsk_username": "test_user",
        "slsk_password": "test_pass",
        "jellyfin_music_library_path": "/jellyfin-music",
    }

    with patch('src.downloader.Downloader') as mock_downloader_class, \
         patch('src.downloader.LibraryScanner') as mock_scanner_class:
        mock_downloader = MagicMock()
        mock_downloader_class.return_value = mock_downloader

        main_run_downloader(db, config)

        mock_downloader_class.assert_called_once()
        assert (
            mock_downloader_class.call_args.kwargs[
                "jellyfin_music_library_dir"
            ]
            == "/jellyfin-music"
        )
        assert (
            mock_downloader_class.call_args.kwargs[
                "lidarr_acquisition_handoff_enabled"
            ]
            is False
        )
        mock_downloader.run_queue.assert_called_once()


# --- Lidarr handoff -------------------------------------------------

def _album_item(release_mbid="rel-mb", query="::ALBUM:: Artist - Album", item_id=1):
    return DownloadItem(
        id=item_id,
        search_query=query,
        playlist_id="COMPLETER",
        mbid_guess=release_mbid,
    )


def _lidarr_ready_downloader(
    db, mock_scanner, temp_dirs, *, handoff_enabled=True
):
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
        lidarr_acquisition_handoff_enabled=handoff_enabled,
        lidarr_quality_profile_id=3,
        lidarr_root_folder_path="/music",
    ), lidarr


def test_try_lidarr_no_client_returns_false(downloader):
    assert downloader._try_lidarr_album(_album_item(), report=lambda m: None) is False


def test_try_lidarr_requires_explicit_acquisition_handoff_flag(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(
        db,
        mock_scanner,
        temp_dirs,
        handoff_enabled=False,
    )

    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is False
    lidarr.ensure_album_monitored.assert_not_called()


@pytest.mark.parametrize("configured_value", [1, "true", "True"])
def test_try_lidarr_handoff_requires_literal_boolean_true(
    db, mock_scanner, temp_dirs, configured_value
):
    dl, lidarr = _lidarr_ready_downloader(
        db,
        mock_scanner,
        temp_dirs,
        handoff_enabled=configured_value,
    )

    assert dl._try_lidarr_album(_album_item(), report=lambda m: None) is False
    lidarr.ensure_album_monitored.assert_not_called()


def test_try_lidarr_never_hands_off_exact_satellite_album(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    item = DownloadItem(
        id=7,
        search_query="::ALBUM:: Artist - Exact Album",
        playlist_id="SATELLITE_ALBUM",
        mbid_guess="exact-release-mbid",
    )

    assert dl._try_lidarr_album(item, report=lambda m: None) is False
    lidarr.ensure_album_monitored.assert_not_called()


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
    jellyfin = MagicMock()
    dl.jellyfin_client = jellyfin
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="COMPLETER",
        mbid_guess="rel-mb",
    ))
    lidarr.ensure_album_monitored.return_value = {"id": 77}

    with patch.object(dl, "_attempt_download") as attempt:
        dl.run_queue()

    attempt.assert_not_called()
    jellyfin.trigger_library_scan.assert_not_called()
    # Item should be gone from the queue after Lidarr handoff
    assert db.get_downloads(status="pending") == []


def test_run_queue_rescans_lidarr_once_after_local_imports(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    db.queue_download(DownloadItem(
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess="",
    ))

    with patch.object(dl, "_attempt_download", return_value=True), \
         patch.object(dl, "_process_success", return_value=1):
        dl.run_queue()

    lidarr.rescan_folders.assert_called_once_with(
        ["/music"],
        add_new_artists=False,
    )


def test_disabled_acquisition_handoff_keeps_lidarr_rescan_active(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(
        db,
        mock_scanner,
        temp_dirs,
        handoff_enabled=False,
    )
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="COMPLETER",
        mbid_guess="release-mbid",
    ))

    with patch.object(dl, "_attempt_download", return_value=True), \
         patch.object(dl, "_process_success", return_value=1):
        dl.run_queue()

    lidarr.ensure_album_monitored.assert_not_called()
    lidarr.rescan_folders.assert_called_once_with(
        ["/music"],
        add_new_artists=False,
    )


def test_lidarr_rescan_failure_does_not_block_jellyfin_refresh(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    jellyfin = MagicMock()
    dl.jellyfin_client = jellyfin
    lidarr.rescan_folders.side_effect = RuntimeError("scan unavailable")
    db.queue_download(DownloadItem(
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess="",
    ))

    with patch.object(dl, "_attempt_download", return_value=True), \
         patch.object(dl, "_process_success", return_value=1):
        dl.run_queue()

    lidarr.rescan_folders.assert_called_once_with(
        ["/music"],
        add_new_artists=False,
    )
    jellyfin.trigger_library_scan.assert_called_once_with()


def test_run_queue_skips_lidarr_rescan_without_media_changes(
    db, mock_scanner, temp_dirs
):
    dl, lidarr = _lidarr_ready_downloader(db, mock_scanner, temp_dirs)
    db.queue_download(DownloadItem(
        search_query="Artist - Track",
        playlist_id="playlist",
        mbid_guess="",
    ))

    with patch.object(dl, "_attempt_download", return_value=True), \
         patch.object(dl, "_process_success", return_value=0):
        dl.run_queue()

    lidarr.rescan_folders.assert_not_called()


def test_default_rescan_root_does_not_enable_lidarr_handoff(
    db, mock_scanner, temp_dirs
):
    lidarr = MagicMock()
    dl = Downloader(
        db=db,
        scanner=mock_scanner,
        slsk_cmd_base=["slsk-batchdl"],
        downloads_dir=temp_dirs["downloads"],
        music_library_dir=temp_dirs["music_library"],
        slsk_username="u",
        slsk_password="p",
        lidarr_client=lidarr,
    )
    db.queue_download(DownloadItem(
        search_query="::ALBUM:: Artist - Album",
        playlist_id="COMPLETER",
        mbid_guess="release-group-mbid",
    ))

    with patch.object(dl, "_attempt_download", return_value=True), \
         patch.object(dl, "_process_success", return_value=1):
        dl.run_queue()

    lidarr.ensure_album_monitored.assert_not_called()
    lidarr.rescan_folders.assert_called_once_with(
        ["/music"],
        add_new_artists=False,
    )


# --- main_run_downloader / Lidarr client wiring ---------------------

def test_build_lidarr_client_skipped_on_satellite():
    from src.downloader import _build_lidarr_client
    config = {
        "device_role": "satellite",
        "is_master": True,
        "lidarr_enabled": True,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_disabled():
    from src.downloader import _build_lidarr_client
    config = {
        "device_role": "master",
        "is_master": False,
        "lidarr_enabled": False,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_creds_missing():
    from src.downloader import _build_lidarr_client
    config = {
        "device_role": "master",
        "is_master": False,
        "lidarr_enabled": True,
        "lidarr_url": "",
        "lidarr_api_key": "k",
    }
    assert _build_lidarr_client(config) is None


def test_build_lidarr_client_skipped_when_ping_fails():
    from src.downloader import _build_lidarr_client
    config = {
        "device_role": "master",
        "is_master": False,
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
        "device_role": "standalone",
        "is_master": False,
        "lidarr_enabled": True,
        "lidarr_url": "http://x",
        "lidarr_api_key": "k",
    }
    with patch("src.downloader.LidarrClient") as cls:
        instance = MagicMock()
        instance.ping.return_value = True
        cls.return_value = instance
        assert _build_lidarr_client(config) is instance
