"""
Background scheduler for periodic multi-device sync.

Wakes every ``sync_interval_seconds`` and invokes the given trigger
callback (typically TaskManager's start_task for a Sync All run).
Skips the tick if the previous run is still in flight so we don't
queue overlapping work.

If ``sync_on_startup`` is set, fires once ~1 s after start() so the
dashboard shows fresh cursors without the user waiting a full interval.

Design notes (docs/roadmap.md #2):
- Disabled when ``sync_interval_seconds`` is ``0`` / missing.
- Runs as a daemon thread so it dies with the process.
- No exponential backoff on failure — logs at WARNING and moves on.
"""

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 1.0


class SyncScheduler:
    def __init__(
        self,
        interval_seconds: int,
        trigger: Callable[[], None],
        run_on_startup: bool = False,
    ):
        self.interval_seconds = int(interval_seconds or 0)
        self.trigger = trigger
        self.run_on_startup = bool(run_on_startup)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.interval_seconds > 0

    def start(self) -> None:
        if not self.enabled:
            logger.info(
                "SyncScheduler disabled (sync_interval_seconds <= 0)."
            )
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            f"SyncScheduler started: interval={self.interval_seconds}s "
            f"run_on_startup={self.run_on_startup}"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        if self.run_on_startup:
            if self._stop.wait(STARTUP_DELAY_SECONDS):
                return
            self._safe_trigger(reason="startup")

        while not self._stop.is_set():
            if self._stop.wait(self.interval_seconds):
                return
            self._safe_trigger(reason="interval")

    def _safe_trigger(self, reason: str) -> None:
        try:
            logger.debug(f"SyncScheduler firing ({reason})")
            self.trigger()
        except Exception as e:
            logger.warning(
                f"SyncScheduler trigger failed ({reason}): {e}", exc_info=True
            )
