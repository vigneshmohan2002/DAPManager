import logging
from contextlib import contextmanager
from unittest.mock import MagicMock

from src.services.scheduler_service import (
    build_library_maintenance_scheduler,
    build_release_watcher_scheduler,
    build_sync_scheduler,
    stop_scheduler,
)


def test_stop_scheduler_is_null_safe_and_contains_stop_failures(caplog):
    stop_scheduler(None)

    scheduler = MagicMock()
    scheduler.stop.side_effect = RuntimeError("still stopping")
    with caplog.at_level(logging.WARNING):
        stop_scheduler(scheduler)

    scheduler.stop.assert_called_once_with()
    assert "Could not stop scheduler cleanly: still stopping" in caplog.text


def test_sync_builder_preserves_startup_policy_and_overlap_gate(caplog):
    scheduler = MagicMock()
    scheduler_factory = MagicMock(return_value=scheduler)
    manager = MagicMock()
    manager.start_task.return_value = (False, "already running")
    target = MagicMock()
    config_context = object()

    built = build_sync_scheduler(
        config_values={
            "sync_interval_seconds": "900",
            "sync_on_startup": True,
        },
        db_path="/tmp/library.db",
        config_context=config_context,
        task_manager=manager,
        task_target=target,
        scheduler_factory=scheduler_factory,
        run_on_startup=False,
    )

    assert built is scheduler
    scheduler_factory.assert_called_once()
    args, kwargs = scheduler_factory.call_args
    assert args[0] == 900
    assert kwargs == {"run_on_startup": False}
    assert scheduler.start.call_count == 0

    with caplog.at_level(logging.INFO):
        assert args[1]() is False
    manager.start_task.assert_called_once_with(
        target,
        ("/tmp/library.db", config_context),
        "Sync All (scheduled)",
    )
    assert "Sync All tick deferred: already running" in caplog.text


def test_sync_builder_defaults_legacy_satellites_to_startup_sync():
    scheduler_factory = MagicMock(return_value=MagicMock())

    build_sync_scheduler(
        config_values={"device_role": "satellite"},
        db_path="/tmp/library.db",
        config_context=object(),
        task_manager=MagicMock(),
        task_target=MagicMock(),
        scheduler_factory=scheduler_factory,
    )

    assert scheduler_factory.call_args.args[0] == 0
    assert scheduler_factory.call_args.kwargs == {"run_on_startup": True}


def test_sync_builder_respects_explicit_satellite_startup_opt_out():
    scheduler_factory = MagicMock(return_value=MagicMock())

    build_sync_scheduler(
        config_values={
            "device_role": "satellite",
            "sync_on_startup": False,
        },
        db_path="/tmp/library.db",
        config_context=object(),
        task_manager=MagicMock(),
        task_target=MagicMock(),
        scheduler_factory=scheduler_factory,
    )

    assert scheduler_factory.call_args.kwargs == {"run_on_startup": False}


def test_release_watcher_builder_enforces_authority_and_configuration():
    scheduler_factory = MagicMock()

    assert build_release_watcher_scheduler(
        config_values={"lidarr_watch_enabled": True},
        is_master=False,
        db_path="/tmp/library.db",
        database_factory=MagicMock(),
        lidarr_client_factory=MagicMock(),
        watch_tick=MagicMock(),
        scheduler_factory=scheduler_factory,
    ) is None
    assert build_release_watcher_scheduler(
        config_values={},
        is_master=True,
        db_path="/tmp/library.db",
        database_factory=MagicMock(),
        lidarr_client_factory=MagicMock(),
        watch_tick=MagicMock(),
        scheduler_factory=scheduler_factory,
    ) is None
    scheduler_factory.assert_not_called()


def test_release_watcher_trigger_resolves_lidarr_and_database_per_tick():
    scheduler = MagicMock()
    scheduler_factory = MagicMock(return_value=scheduler)
    client = object()
    lidarr_client_factory = MagicMock(return_value=client)
    watch_tick = MagicMock()
    database = object()
    opened_paths = []

    @contextmanager
    def database_factory(path):
        opened_paths.append(path)
        yield database

    built = build_release_watcher_scheduler(
        config_values={
            "lidarr_watch_enabled": True,
            "lidarr_watch_interval_seconds": "1234",
        },
        is_master=True,
        db_path="/tmp/library.db",
        database_factory=database_factory,
        lidarr_client_factory=lidarr_client_factory,
        watch_tick=watch_tick,
        scheduler_factory=scheduler_factory,
    )

    assert built is scheduler
    args, kwargs = scheduler_factory.call_args
    assert args[0] == 1234
    assert kwargs == {"run_on_startup": False}
    args[1]()
    lidarr_client_factory.assert_called_once_with(
        {
            "lidarr_watch_enabled": True,
            "lidarr_watch_interval_seconds": "1234",
        }
    )
    assert opened_paths == ["/tmp/library.db"]
    watch_tick.assert_called_once_with(database, client)


def test_release_watcher_trigger_skips_database_when_lidarr_is_unavailable():
    scheduler_factory = MagicMock(return_value=MagicMock())
    database_factory = MagicMock()
    watch_tick = MagicMock()

    build_release_watcher_scheduler(
        config_values={"lidarr_watch_enabled": True},
        is_master=True,
        db_path="/tmp/library.db",
        database_factory=database_factory,
        lidarr_client_factory=MagicMock(return_value=None),
        watch_tick=watch_tick,
        scheduler_factory=scheduler_factory,
    )

    trigger = scheduler_factory.call_args.args[1]
    assert trigger() is None
    database_factory.assert_not_called()
    watch_tick.assert_not_called()


def test_maintenance_builder_preserves_interval_delay_and_overlap_gate():
    scheduler = MagicMock()
    scheduler_factory = MagicMock(return_value=scheduler)
    manager = MagicMock()
    manager.start_task.return_value = (True, "Task started.")
    target = MagicMock()
    config_context = object()
    interval_resolver = MagicMock(return_value=604800)
    config_values = {"library_maintenance_on_startup": True}

    built = build_library_maintenance_scheduler(
        config_values=config_values,
        is_master=True,
        db_path="/tmp/library.db",
        config_context=config_context,
        task_manager=manager,
        task_target=target,
        interval_resolver=interval_resolver,
        scheduler_factory=scheduler_factory,
    )

    assert built is scheduler
    interval_resolver.assert_called_once_with(config_values)
    args, kwargs = scheduler_factory.call_args
    assert args[0] == 604800
    assert kwargs == {
        "run_on_startup": True,
        "startup_delay_seconds": 5.0,
    }
    assert args[1]() is True
    manager.start_task.assert_called_once_with(
        target,
        ("/tmp/library.db", config_context),
        "Library maintenance (scheduled)",
    )


def test_maintenance_builder_is_master_only_and_zero_disables():
    scheduler_factory = MagicMock()
    common = {
        "config_values": {},
        "db_path": "/tmp/library.db",
        "config_context": object(),
        "task_manager": MagicMock(),
        "task_target": MagicMock(),
        "scheduler_factory": scheduler_factory,
    }

    assert build_library_maintenance_scheduler(
        **common,
        is_master=False,
        interval_resolver=MagicMock(return_value=604800),
    ) is None
    assert build_library_maintenance_scheduler(
        **common,
        is_master=True,
        interval_resolver=MagicMock(return_value=0),
    ) is None
    scheduler_factory.assert_not_called()
