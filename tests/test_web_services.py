import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from src.services import config_service

from src.services.library_service import (
    availability_for,
    is_master_configured,
    public_track_row,
)
from src.services.media_proxy_service import (
    build_upstream_headers,
    guess_audio_mime,
)
from src.services.config_service import (
    build_first_run_config,
    build_public_config,
    merge_config_update,
    normalize_config_update,
    reload_runtime_config,
)
from src.services.task_service import TaskManager


def _wait_until_idle(manager: TaskManager, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while manager.is_running and time.monotonic() < deadline:
        time.sleep(0.005)
    assert manager.is_running is False


def test_is_master_configured_requires_a_real_non_empty_mapping_value():
    assert is_master_configured(None) is False
    assert is_master_configured("https://master.invalid") is False
    assert is_master_configured({}) is False
    assert is_master_configured({"master_url": "   "}) is False
    assert is_master_configured({"master_url": " https://master.test "}) is True


def test_availability_prefers_local_then_drive_then_master():
    assert availability_for({"local_path": "/music/song.flac"}, True) == "local"
    assert availability_for(
        {"local_path": "", "dap_path": "/Volumes/DAP/song.flac"}, True
    ) == "drive"
    assert availability_for({"local_path": None, "dap_path": None}, True) == "remote"
    assert availability_for({"local_path": None, "dap_path": None}, False) == "unavailable"


def test_authority_availability_prevents_dead_remote_rows():
    row = {
        "local_path": None,
        "dap_path": None,
        "master_streamable": 0,
    }

    assert availability_for(row, True) == "unavailable"


def test_local_copy_wins_when_authority_source_is_unavailable():
    row = {
        "local_path": "/music/local.flac",
        "dap_path": None,
        "master_streamable": 0,
    }

    assert availability_for(row, True) == "local"


def test_public_track_row_preserves_wire_shape_and_hides_paths():
    row = {
        "mbid": "track-1",
        "title": "Track",
        "artist": "Artist",
        "album": "Album",
        "track_number": 2,
        "disc_number": 1,
        "album_id": "release-1",
        "local_path": "/private/music.flac",
        "dap_path": "/private/dap.flac",
        "is_liked": 1,
    }

    assert public_track_row(row, has_master=True) == {
        "mbid": "track-1",
        "title": "Track",
        "artist": "Artist",
        "album": "Album",
        "track_number": 2,
        "disc_number": 1,
        "album_id": "release-1",
        "availability": "local",
        "is_liked": True,
    }


def test_build_public_config_applies_defaults_and_redacts_secrets():
    payload = build_public_config({
        "music_library_path": "/music",
        "slsk_password": "secret",
        "is_master": True,
    })

    assert payload["success"] is True
    assert payload["config"]["music_library_path"] == "/music"
    assert payload["config"]["slsk_password"] == ""
    assert payload["config"]["device_role"] == "master"
    assert payload["config"]["sync_on_startup"] is False
    assert "library_maintenance_interval_seconds" in payload["config"]
    assert payload["config"]["lidarr_acquisition_handoff_enabled"] is False
    assert "lidarr_acquisition_handoff_enabled" in payload["editable_keys"]
    assert "lidarr_acquisition_handoff_enabled" in payload["bool_keys"]
    lidarr_groups = [
        group for group in payload["groups"]
        if group["label"] == "Lidarr Sidecar (master only)"
    ]
    assert len(lidarr_groups) == 1
    assert (
        "lidarr_acquisition_handoff_enabled"
        in lidarr_groups[0]["keys"]
    )
    assert "slsk_password" in payload["secret_keys"]


def test_build_public_config_exposes_satellite_startup_sync_default():
    payload = build_public_config({"device_role": "satellite"})

    assert payload["config"]["sync_on_startup"] is True


def test_config_update_normalizes_role_and_preserves_blank_secrets():
    update = normalize_config_update({
        "device_role": " SATELLITE ",
        "slsk_password": "",
        "music_library_path": "/new/music",
        "database_file": "ignored.db",
    })

    merged, changed = merge_config_update(
        {
            "is_master": True,
            "slsk_password": "existing",
            "music_library_path": "/music",
            "database_file": "library.db",
        },
        update,
    )

    assert changed == ["device_role", "music_library_path"]
    assert merged["device_role"] == "satellite"
    assert merged["is_master"] is False
    assert merged["slsk_password"] == "existing"
    assert merged["database_file"] == "library.db"


def test_build_first_run_config_filters_fields_and_owns_database_path(tmp_path):
    builder = Mock(return_value={
        "device_role": "satellite",
        "is_master": True,
    })
    target = tmp_path / "nested" / "config.json"

    result = build_first_run_config(
        str(target),
        {
            "role": " SATELLITE ",
            "music_library_path": "/music",
            "lidarr_acquisition_handoff_enabled": True,
            "database_file": "/client-selected.db",
        },
        builder,
    )

    builder.assert_called_once_with(
        "satellite",
        database_file=str(target.with_name("dap_library.db")),
        music_library_path="/music",
        lidarr_acquisition_handoff_enabled=True,
    )
    assert result["device_role"] == "satellite"
    assert result["is_master"] is False


def test_reload_runtime_config_owns_scheduler_dependency_policy(monkeypatch):
    class FakeConfigManager:
        _instance = None

        def __init__(self):
            self.reload_count = 0

        def _load_config(self):
            self.reload_count += 1

    monkeypatch.setattr(config_service, "ConfigManager", FakeConfigManager)
    runtime = FakeConfigManager()
    sync_restart = Mock()
    release_restart = Mock()
    maintenance_restart = Mock()

    reload_runtime_config(
        runtime,
        {
            "sync_interval_seconds",
            "lidarr_watch_interval_seconds",
            "library_maintenance_on_startup",
        },
        start_sync_scheduler=sync_restart,
        start_release_watcher=release_restart,
        start_library_maintenance_scheduler=maintenance_restart,
    )

    assert runtime.reload_count == 1
    assert FakeConfigManager._instance is runtime
    sync_restart.assert_called_once_with(run_on_startup=False)
    release_restart.assert_called_once_with()
    maintenance_restart.assert_called_once_with(run_on_startup=False)


def test_media_proxy_headers_forward_only_allowed_values_and_authenticate():
    assert build_upstream_headers(
        {
            "Range": "bytes=4-8",
            "If-None-Match": '"cover-v1"',
            "X-Unrelated": "drop-me",
        },
        forwarded=("Range", "If-None-Match"),
        api_token=" secret-token ",
        defaults={"Accept": "application/json"},
    ) == {
        "Accept": "application/json",
        "Range": "bytes=4-8",
        "If-None-Match": '"cover-v1"',
        "Authorization": "Bearer secret-token",
    }


def test_guess_audio_mime_preserves_supported_and_fallback_types():
    assert guess_audio_mime("/music/track.FLAC") == "audio/flac"
    assert guess_audio_mime("/music/track.m4a") == "audio/mp4"
    assert guess_audio_mime("/music/track.opus") == "audio/ogg"
    assert guess_audio_mime("/music/track.unknown") == "application/octet-stream"


def test_task_manager_injects_progress_callback_and_resets_state():
    manager = TaskManager()
    finished = threading.Event()

    def job(progress_callback):
        progress_callback({"message": "Halfway", "detail": "1 of 2"})
        finished.set()

    started, message = manager.start_task(job, task_name="Test job")

    assert (started, message) == (True, "Task started.")
    assert finished.wait(timeout=1)
    _wait_until_idle(manager)
    assert manager.is_running is False
    assert manager.current_task is None
    assert manager.message == "Test job completed successfully."
    assert manager.progress_detail == "1 of 2"


def test_task_manager_preserves_truthful_structured_result_message():
    manager = TaskManager()

    class PartialFailure:
        task_message = "Download queue finished with failures. Success: 1, Failed: 2."

    started, message = manager.start_task(
        lambda: PartialFailure(),
        task_name="Download Queue",
    )

    assert (started, message) == (True, "Task started.")
    _wait_until_idle(manager)
    assert manager.message == (
        "Download queue finished with failures. Success: 1, Failed: 2."
    )


def test_web_sync_wrapper_returns_structured_nested_outcome(monkeypatch):
    import web_server

    outcome = SimpleNamespace(task_message="Download queue failed safely.")
    database = MagicMock()
    database.__enter__.return_value = "db"
    run_sync = Mock(return_value=outcome)
    monkeypatch.setattr(
        web_server,
        "DatabaseManager",
        Mock(return_value=database),
        raising=False,
    )
    monkeypatch.setattr(web_server, "main_run_sync", run_sync, raising=False)
    config = SimpleNamespace(_config={"music_library_path": "/music"})

    result = web_server.run_sync(
        "library.db",
        config,
        "library",
        "flac",
        reconcile=True,
    )

    assert result is outcome
    run_sync.assert_called_once_with(
        "db",
        config._config,
        sync_mode="library",
        conversion_format="flac",
        reconcile=True,
    )


def test_web_album_completion_wrapper_returns_pipeline_outcome(monkeypatch):
    import web_server

    outcome = SimpleNamespace(task_message="Download queue failed safely.")
    pipeline = Mock(return_value=outcome)
    monkeypatch.setattr(
        web_server.album_task_service,
        "run_album_completion_pipeline",
        pipeline,
    )
    monkeypatch.setattr(web_server, "DatabaseManager", Mock(), raising=False)
    monkeypatch.setattr(web_server, "complete_albums_logic", Mock(), raising=False)
    monkeypatch.setattr(web_server, "main_run_downloader", Mock(), raising=False)
    monkeypatch.setattr(web_server, "main_scan_library", Mock(), raising=False)
    config = SimpleNamespace(_config={"music_library_path": "/music"})

    result = web_server.run_complete_albums(
        "library.db",
        config,
        run_downloads=True,
    )

    assert result is outcome
    assert pipeline.call_args.kwargs["run_downloads"] is True


def test_task_manager_rejects_overlapping_work():
    manager = TaskManager()
    release = threading.Event()
    started = threading.Event()

    def blocking_job():
        started.set()
        release.wait(timeout=1)

    assert manager.start_task(blocking_job, task_name="First") == (
        True,
        "Task started.",
    )
    assert started.wait(timeout=1)

    assert manager.start_task(lambda: None, task_name="Second") == (
        False,
        "Task 'First' is already running.",
    )

    release.set()
    _wait_until_idle(manager)
