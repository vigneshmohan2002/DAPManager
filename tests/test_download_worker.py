import threading
from unittest.mock import MagicMock, patch

from src.db_manager import DatabaseManager, DownloadItem
from src.download_worker import AutomaticDownloadWorker
from src.downloader import DownloadRunSummary


def _queue(db_path, count=2):
    with DatabaseManager(str(db_path)) as db:
        ids = [
            db.queue_download(DownloadItem(
                search_query=f"Artist - Album {number}",
                playlist_id="test",
                mbid_guess="",
            ))
            for number in range(count)
        ]
        db.set_download_worker_paused(False)
    return ids


def test_worker_runs_two_acquisitions_concurrently_when_disk_allows(tmp_path):
    db_path = tmp_path / "library.db"
    ids = _queue(db_path)
    barrier = threading.Barrier(2)
    seen = []

    def runner(_db, _config, *, include_item_ids):
        seen.append(include_item_ids[0])
        barrier.wait(timeout=2)
        return DownloadRunSummary(
            eligible_count=1,
            attempted_count=1,
            success_count=1,
        )

    worker = AutomaticDownloadWorker(
        str(db_path),
        {"downloads_path": str(tmp_path), "download_worker_max_acquisitions": 2},
        runner=runner,
    )
    usage = MagicMock(free=40 * 1024 ** 3)
    with patch("src.download_worker.shutil.disk_usage", return_value=usage):
        assert worker.run_available_once() == 2

    assert sorted(seen) == ids


def test_worker_reduces_to_one_acquisition_when_second_reserve_is_missing(
    tmp_path,
):
    db_path = tmp_path / "library.db"
    _queue(db_path)
    seen = []

    def runner(_db, _config, *, include_item_ids):
        seen.extend(include_item_ids)
        return DownloadRunSummary(attempted_count=1, success_count=1)

    worker = AutomaticDownloadWorker(
        str(db_path),
        {"downloads_path": str(tmp_path), "download_worker_max_acquisitions": 2},
        runner=runner,
    )
    usage = MagicMock(free=27 * 1024 ** 3)
    with patch("src.download_worker.shutil.disk_usage", return_value=usage):
        assert worker.run_available_once() == 1

    assert len(seen) == 1


def test_worker_does_nothing_while_durably_paused(tmp_path):
    db_path = tmp_path / "library.db"
    with DatabaseManager(str(db_path)) as db:
        db.queue_download(DownloadItem("Artist - Album", "test", ""))
    runner = MagicMock()
    worker = AutomaticDownloadWorker(
        str(db_path), {"downloads_path": str(tmp_path)}, runner=runner
    )

    assert worker.run_available_once() == 0
    runner.assert_not_called()
