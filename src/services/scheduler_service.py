"""Typed scheduler construction independent of Flask module state.

The web adapter deliberately retains ownership of scheduler instances.  These
builders only encode scheduling policy and construct trigger callbacks, which
keeps replacement order (stop, clear the public global, build, assign, start)
explicit at the integration boundary.
"""

import logging
from contextlib import AbstractContextManager
from typing import Any, Callable, Optional, Protocol, Tuple

from src.contracts import ConfigMapping
from src.config_manager import sync_on_startup_from_config


logger = logging.getLogger(__name__)

TaskCallable = Callable[..., Any]
DatabaseContextFactory = Callable[[str], AbstractContextManager[Any]]


class TaskStarter(Protocol):
    """Smallest background-task interface consumed by scheduled jobs."""

    def start_task(
        self,
        task_func: TaskCallable,
        args: Tuple[Any, ...] = (),
        task_name: str = "Task",
    ) -> Tuple[bool, str]:
        """Start a task or reject it when another task is active."""
        ...


class Scheduler(Protocol):
    """Lifecycle exposed by :class:`src.sync_scheduler.SyncScheduler`."""

    def start(self) -> None:
        """Start the scheduler loop."""
        ...

    def stop(self) -> None:
        """Stop the scheduler loop."""
        ...


SchedulerFactory = Callable[..., Scheduler]


def stop_scheduler(
    instance: Optional[Scheduler],
    *,
    event_logger: logging.Logger = logger,
) -> None:
    """Stop an existing scheduler without turning shutdown into a failure."""
    if instance is None:
        return
    try:
        instance.stop()
    except Exception as exc:
        event_logger.warning("Could not stop scheduler cleanly: %s", exc)


def build_sync_scheduler(
    *,
    config_values: ConfigMapping,
    db_path: str,
    config_context: Any,
    task_manager: TaskStarter,
    task_target: TaskCallable,
    scheduler_factory: SchedulerFactory,
    run_on_startup: Optional[bool] = None,
    event_logger: logging.Logger = logger,
) -> Scheduler:
    """Construct the periodic Sync All scheduler without starting it."""
    interval = int(config_values.get("sync_interval_seconds") or 0)
    on_startup = (
        sync_on_startup_from_config(config_values)
        if run_on_startup is None
        else bool(run_on_startup)
    )

    def trigger() -> bool:
        started, message = task_manager.start_task(
            task_target,
            (db_path, config_context),
            "Sync All (scheduled)",
        )
        if not started:
            event_logger.info("Sync All tick deferred: %s", message)
        return started

    return scheduler_factory(
        interval,
        trigger,
        run_on_startup=on_startup,
    )


def build_release_watcher_scheduler(
    *,
    config_values: ConfigMapping,
    is_master: bool,
    db_path: str,
    database_factory: DatabaseContextFactory,
    lidarr_client_factory: Callable[[ConfigMapping], Any],
    watch_tick: Callable[[Any, Any], Any],
    scheduler_factory: SchedulerFactory,
    event_logger: logging.Logger = logger,
) -> Optional[Scheduler]:
    """Construct the enabled master release watcher, or return ``None``."""
    if not is_master:
        event_logger.info("release_watcher disabled on non-authority device.")
        return None
    if not bool(config_values.get("lidarr_watch_enabled") or False):
        event_logger.info("release_watcher disabled (lidarr_watch_enabled != true).")
        return None

    interval = int(config_values.get("lidarr_watch_interval_seconds") or 3600)

    def trigger() -> None:
        client = lidarr_client_factory(config_values)
        if client is None:
            event_logger.debug("release_watcher: Lidarr unavailable; skipping tick.")
            return
        with database_factory(db_path) as db:
            watch_tick(db, client)

    return scheduler_factory(interval, trigger, run_on_startup=False)


def build_library_maintenance_scheduler(
    *,
    config_values: ConfigMapping,
    is_master: bool,
    db_path: str,
    config_context: Any,
    task_manager: TaskStarter,
    task_target: TaskCallable,
    interval_resolver: Callable[[ConfigMapping], int],
    scheduler_factory: SchedulerFactory,
    run_on_startup: Optional[bool] = None,
    event_logger: logging.Logger = logger,
) -> Optional[Scheduler]:
    """Construct enabled master library maintenance, or return ``None``."""
    if not is_master:
        event_logger.info("Library maintenance disabled on non-master device.")
        return None

    interval = interval_resolver(config_values)
    if interval <= 0:
        event_logger.info(
            "Library maintenance disabled "
            "(library_maintenance_interval_seconds <= 0)."
        )
        return None

    on_startup = (
        bool(config_values.get("library_maintenance_on_startup") or False)
        if run_on_startup is None
        else bool(run_on_startup)
    )

    def trigger() -> bool:
        started, message = task_manager.start_task(
            task_target,
            (db_path, config_context),
            "Library maintenance (scheduled)",
        )
        if not started:
            event_logger.info("Library maintenance tick deferred: %s", message)
        return started

    return scheduler_factory(
        interval,
        trigger,
        run_on_startup=on_startup,
        startup_delay_seconds=5.0,
    )
