"""Scheduled MusicBrainz + Daily Mix maintenance coverage."""

import threading
from unittest.mock import MagicMock, patch

import web_server
from src.library_maintenance import (
    DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
    maintenance_interval_seconds,
    run_library_maintenance,
)


def test_maintenance_interval_defaults_weekly_and_zero_disables():
    assert maintenance_interval_seconds({}) == DEFAULT_MAINTENANCE_INTERVAL_SECONDS
    assert maintenance_interval_seconds({
        "library_maintenance_interval_seconds": "3600",
    }) == 3600
    assert maintenance_interval_seconds({
        "library_maintenance_interval_seconds": 0,
    }) == 0
    assert maintenance_interval_seconds({
        "library_maintenance_interval_seconds": "bad",
    }) == DEFAULT_MAINTENANCE_INTERVAL_SECONDS


def test_maintenance_refreshes_tags_before_regenerating_mixes():
    db = MagicMock()
    events = []

    def backfill(*args, **kwargs):
        events.append(("tags", kwargs["max_age_days"]))
        return {"tagged": 12}

    def regenerate(*args, **kwargs):
        events.append(("mixes", None))
        return {"mixes": 3}

    progress = []
    with patch(
        "src.library_maintenance.backfill_artist_tags",
        side_effect=backfill,
    ), patch(
        "src.library_maintenance.regenerate_daily_mixes",
        side_effect=regenerate,
    ):
        summary = run_library_maintenance(
            db,
            {"artist_tag_max_age_days": "14"},
            progress_callback=progress.append,
        )

    assert events == [("tags", 14), ("mixes", None)]
    assert summary == {
        "status": "ok",
        "tag_backfill": {"tagged": 12},
        "daily_mixes": {"mixes": 3},
    }
    assert any("complete" in item["message"].lower() for item in progress)


def test_maintenance_skips_overlapping_run():
    entered = threading.Event()
    release = threading.Event()
    first_result = []

    def blocking_backfill(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return {"tagged": 0}

    with patch(
        "src.library_maintenance.backfill_artist_tags",
        side_effect=blocking_backfill,
    ), patch(
        "src.library_maintenance.regenerate_daily_mixes",
        return_value={"mixes": 0},
    ):
        thread = threading.Thread(
            target=lambda: first_result.append(
                run_library_maintenance(MagicMock(), {})
            )
        )
        thread.start()
        assert entered.wait(timeout=1)

        overlapping = run_library_maintenance(MagicMock(), {})
        assert overlapping == {
            "status": "skipped",
            "reason": "already_running",
        }

        release.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert first_result[0]["status"] == "ok"


def test_web_scheduler_is_master_only(monkeypatch):
    cfg = MagicMock()
    cfg.is_master = False
    cfg._config = {}
    monkeypatch.setattr(web_server, "config", cfg)
    monkeypatch.setattr(web_server, "library_maintenance_scheduler", MagicMock())

    web_server._start_library_maintenance_scheduler()

    assert web_server.library_maintenance_scheduler is None


def test_web_scheduler_uses_task_manager_overlap_gate(monkeypatch):
    cfg = MagicMock()
    cfg.is_master = True
    cfg.db_path = "/tmp/test.db"
    cfg._config = {
        "library_maintenance_interval_seconds": 1234,
        "library_maintenance_on_startup": True,
    }
    manager = MagicMock()
    manager.start_task.return_value = (False, "already running")
    scheduler = MagicMock()

    monkeypatch.setattr(web_server, "config", cfg)
    monkeypatch.setattr(web_server, "task_manager", manager)
    with patch("src.sync_scheduler.SyncScheduler", return_value=scheduler) as cls:
        web_server._start_library_maintenance_scheduler()

    trigger = cls.call_args.args[1]
    assert cls.call_args.args[0] == 1234
    assert cls.call_args.kwargs["run_on_startup"] is True
    assert cls.call_args.kwargs["startup_delay_seconds"] == 5.0
    scheduler.start.assert_called_once_with()

    trigger()
    manager.start_task.assert_called_once_with(
        web_server.run_library_maintenance_task,
        (cfg.db_path, cfg),
        "Library maintenance (scheduled)",
    )
