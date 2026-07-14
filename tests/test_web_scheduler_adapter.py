from unittest.mock import MagicMock, patch

import pytest

import web_server


def _scheduler_config(**values):
    config = MagicMock()
    config.db_path = "/tmp/library.db"
    config.is_master = True
    config._config = values
    return config


def test_sync_scheduler_adapter_stops_replaces_starts_and_keeps_target(monkeypatch):
    config = _scheduler_config(
        sync_interval_seconds="900",
        sync_on_startup=True,
    )
    manager = MagicMock()
    manager.start_task.return_value = (True, "Task started.")
    previous = MagicMock()
    replacement = MagicMock()
    monkeypatch.setattr(web_server, "config", config)
    monkeypatch.setattr(web_server, "task_manager", manager)
    monkeypatch.setattr(web_server, "sync_scheduler", previous)

    with patch("src.sync_scheduler.SyncScheduler", return_value=replacement) as factory:
        web_server._start_sync_scheduler(run_on_startup=False)

    previous.stop.assert_called_once_with()
    replacement.start.assert_called_once_with()
    assert web_server.sync_scheduler is replacement
    interval, trigger = factory.call_args.args
    assert interval == 900
    assert factory.call_args.kwargs == {"run_on_startup": False}
    assert trigger() is True
    manager.start_task.assert_called_once_with(
        web_server.run_sync_all,
        (config.db_path, config),
        "Sync All (scheduled)",
    )


def test_release_watcher_adapter_resolves_dependencies_for_each_tick(monkeypatch):
    config = _scheduler_config(
        lidarr_watch_enabled=True,
        lidarr_watch_interval_seconds="1200",
    )
    previous = MagicMock()
    replacement = MagicMock()
    database_manager = MagicMock()
    database = database_manager.return_value.__enter__.return_value
    lidarr = object()
    monkeypatch.setattr(web_server, "config", config)
    monkeypatch.setattr(web_server, "release_watcher_scheduler", previous)
    monkeypatch.setattr(web_server, "DatabaseManager", database_manager)

    with patch("src.sync_scheduler.SyncScheduler", return_value=replacement) as factory, \
         patch("src.downloader._build_lidarr_client", return_value=lidarr) as client_factory, \
         patch("src.release_watcher.run_watch_tick") as watch_tick:
        web_server._start_release_watcher()
        trigger = factory.call_args.args[1]
        trigger()

    previous.stop.assert_called_once_with()
    replacement.start.assert_called_once_with()
    assert web_server.release_watcher_scheduler is replacement
    client_factory.assert_called_once_with(config._config)
    database_manager.assert_called_once_with(config.db_path)
    watch_tick.assert_called_once_with(database, lidarr)


def test_scheduler_adapter_leaves_public_slot_empty_when_build_fails(monkeypatch):
    previous = MagicMock()
    monkeypatch.setattr(web_server, "config", _scheduler_config())
    monkeypatch.setattr(web_server, "task_manager", MagicMock())
    monkeypatch.setattr(web_server, "sync_scheduler", previous)

    with patch("web_server.build_sync_scheduler", side_effect=ValueError("bad interval")):
        with pytest.raises(ValueError, match="bad interval"):
            web_server._start_sync_scheduler()

    previous.stop.assert_called_once_with()
    assert web_server.sync_scheduler is None
