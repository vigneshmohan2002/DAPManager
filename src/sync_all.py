"""
Sync orchestrator: runs the four multi-device sync operations in the
right order as a single user-visible task.

Sequencing (see docs/roadmap.md #1):
  1. pull catalog   — tracks must exist before playlist membership lands
  2. pull playlists — full-membership replace
  3. push playlists — satellite edits back up to the master
  4. report inventory — opt-in inventory snapshot

Each step is gated by existing config rules (master_url for the first
three; report_inventory_to_host for the fourth). Steps that don't apply
are recorded as ``skipped`` rather than failing the whole run. If a step
raises, the remaining steps still execute so a transient failure on one
doesn't block the others.
"""

import logging
from typing import Callable, Dict, List, Optional

from .catalog_sync import (
    main_run_catalog_pull,
    main_run_playlist_pull,
    main_run_playlist_push,
)
from .db_manager import DatabaseManager
from .inventory_sync import main_run_inventory_report

logger = logging.getLogger(__name__)


def _bool(val) -> bool:
    return bool(val) and val not in ("false", "False", "0", 0)


def main_run_sync_all(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> Dict:
    """Run all four sync operations applicable to this device.

    Returns ``{steps: [{name, status, message, summary?}]}`` where
    ``status`` is one of 'ok', 'skipped', 'error'.
    """
    results: List[Dict] = []

    master_url = (config.get("master_url") or "").strip()
    is_master = _bool(config.get("is_master"))
    # report_inventory_to_host defaults to True on the master, False otherwise.
    report_inv = config.get("report_inventory_to_host")
    if report_inv is None:
        report_inv = is_master
    report_inv = _bool(report_inv)

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback({"message": msg})

    def _run(name: str, fn: Callable[[], Dict]):
        _report(f"Sync All: {name}")
        try:
            summary = fn() or {}
            results.append({"name": name, "status": "ok", "summary": summary})
        except Exception as e:
            logger.warning(f"Sync All: {name} failed: {e}", exc_info=True)
            results.append({"name": name, "status": "error", "message": str(e)})

    def _skip(name: str, reason: str):
        results.append({"name": name, "status": "skipped", "message": reason})

    # Pulls only make sense for satellites (devices that point at a master).
    if master_url:
        _run("pull_catalog", lambda: main_run_catalog_pull(db, config))
        _run("pull_playlists", lambda: main_run_playlist_pull(db, config))
        _run("push_playlists", lambda: main_run_playlist_push(db, config))
    else:
        reason = "master_url not configured"
        _skip("pull_catalog", reason)
        _skip("pull_playlists", reason)
        _skip("push_playlists", reason)

    if report_inv:
        _run("report_inventory", lambda: main_run_inventory_report(db, config))
    else:
        _skip("report_inventory", "report_inventory_to_host is disabled")

    summary_line = ", ".join(
        f"{r['name']}={r['status']}" for r in results
    )
    _report(f"Sync All finished: {summary_line}")
    return {"steps": results}
