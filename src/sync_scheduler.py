"""
Background scheduler for periodic multi-device sync.

Wakes every ``sync_interval_seconds`` and invokes the given trigger
callback (typically TaskManager's start_task for a Sync All run).
Skips the tick if the previous run is still in flight so we don't
queue overlapping work.

If ``sync_on_startup`` is set, fires once ~1 s after start() so the
dashboard shows fresh cursors without the user waiting a full interval.

Design notes (docs/roadmap.md #2):
- With no interval, an enabled startup run executes once and then exits.
- Otherwise disabled when ``sync_interval_seconds`` is ``0`` / missing.
- Runs as a daemon thread so it dies with the process.
- No exponential backoff on failure — logs at WARNING and moves on.
"""

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 1.0
STARTUP_RETRY_SECONDS = 30.0


class SyncScheduler:
    def __init__(
        self,
        interval_seconds: int,
        trigger: Callable[[], Optional[bool]],
        run_on_startup: bool = False,
        startup_delay_seconds: Optional[float] = None,
    ):
        self.interval_seconds = int(interval_seconds or 0)
        self.trigger = trigger
        self.run_on_startup = bool(run_on_startup)
        self.startup_delay_seconds = (
            float(STARTUP_DELAY_SECONDS)
            if startup_delay_seconds is None
            else max(0.0, float(startup_delay_seconds))
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.interval_seconds > 0 or self.run_on_startup

    def start(self) -> None:
        if not self.enabled:
            logger.info(
                "SyncScheduler disabled (no startup run and "
                "sync_interval_seconds <= 0)."
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
            if self._stop.wait(self.startup_delay_seconds):
                return
            # TaskManager can be occupied by another startup job. A callback
            # may return False to ask for a bounded retry rather than losing a
            # weekly/monthly job until its entire interval elapses.
            while not self._safe_trigger(reason="startup"):
                if self._stop.wait(STARTUP_RETRY_SECONDS):
                    return

        if self.interval_seconds <= 0:
            return

        while not self._stop.is_set():
            if self._stop.wait(self.interval_seconds):
                return
            self._safe_trigger(reason="interval")

    def _safe_trigger(self, reason: str) -> bool:
        try:
            logger.debug(f"SyncScheduler firing ({reason})")
            return self.trigger() is not False
        except Exception as e:
            logger.warning(
                f"SyncScheduler trigger failed ({reason}): {e}", exc_info=True
            )
            # Exceptions are left to the next ordinary interval rather than
            # hammered every retry window.
            return True
