import threading
import time

from src.services.library_service import (
    availability_for,
    is_master_configured,
    public_track_row,
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
