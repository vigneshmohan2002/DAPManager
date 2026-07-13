"""Periodic master-side genre metadata and Daily Mix maintenance.

The two operations deliberately share one run: Daily Mixes must be rebuilt
*after* the MusicBrainz cache is refreshed, and a single non-blocking lock
prevents duplicate scheduler instances from doing the expensive backfill at
the same time within a process.
"""

import logging
import threading
from typing import Callable, Optional

from .daily_mixes import regenerate_daily_mixes
from .db_manager import DatabaseManager
from .genre_backfill import backfill_artist_tags


logger = logging.getLogger(__name__)

DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_ARTIST_TAG_MAX_AGE_DAYS = 30

_RUN_LOCK = threading.Lock()


def _nonnegative_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def maintenance_interval_seconds(config: dict) -> int:
    """Configured cadence, defaulting to weekly; ``0`` disables it."""
    return _nonnegative_int(
        (config or {}).get(
            "library_maintenance_interval_seconds",
            DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
        ),
        DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
    )


def run_library_maintenance(
    db: DatabaseManager,
    config: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Refresh stale artist tags, then regenerate the static Daily Mixes.

    Returns ``status=skipped`` when another maintenance run is already in
    flight.  This makes repeated scheduler ticks safe even when the first
    MusicBrainz pass takes longer than its configured interval.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        logger.info("Library maintenance skipped: a run is already active")
        return {"status": "skipped", "reason": "already_running"}

    cfg = config or {}
    max_age_days = _nonnegative_int(
        cfg.get("artist_tag_max_age_days", DEFAULT_ARTIST_TAG_MAX_AGE_DAYS),
        DEFAULT_ARTIST_TAG_MAX_AGE_DAYS,
    )

    def _report(message: str, detail: str = "") -> None:
        logger.info(message)
        if progress_callback:
            payload = {"message": message}
            if detail:
                payload["detail"] = detail
            progress_callback(payload)

    try:
        _report("Library maintenance: refreshing artist tags")
        tag_summary = backfill_artist_tags(
            db,
            progress_callback=progress_callback,
            incremental=True,
            max_age_days=max_age_days,
        )

        _report("Library maintenance: regenerating Daily Mixes")
        mix_summary = regenerate_daily_mixes(db)
        _report(
            "Library maintenance complete",
            detail=(
                f"{tag_summary.get('tagged', 0)} tags persisted; "
                f"{mix_summary.get('mixes', 0)} mixes generated"
            ),
        )
        return {
            "status": "ok",
            "tag_backfill": tag_summary,
            "daily_mixes": mix_summary,
        }
    finally:
        _RUN_LOCK.release()
