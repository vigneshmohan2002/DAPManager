"""
Sync orchestrator: runs the multi-device sync operations in the
right order as a single user-visible task.

Sequencing (see docs/roadmap.md #1):
  1. pull catalog   — tracks must exist before playlist membership lands
  2. pull artist tags — master-owned MusicBrainz metadata
  3. pull playlists — full-membership replace
  4. push playlists — satellite edits back up to the master
  5. pull lyrics — cached/manual lyric metadata
  6. report inventory — opt-in inventory snapshot

Each step is gated by existing config rules (master_url for the first
three; report_inventory_to_host for the fourth). Steps that don't apply
are recorded as ``skipped`` rather than failing the whole run. If a step
raises, the remaining steps still execute so a transient failure on one
doesn't block the others.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, cast

from .catalog_sync import (
    main_run_artist_tags_pull,
    main_run_catalog_pull,
    main_run_lyrics_pull,
    main_run_playlist_pull,
    main_run_playlist_push,
)
from .contribution_sync import main_run_contribute
from .config_manager import device_role_from_config, is_authority_config
from .contracts import (
    DeviceRole,
    JSONObject,
    ProgressEvent,
    SyncOperation,
    SyncStepResult,
)
from .db_manager import DatabaseManager
from .inventory_sync import main_run_inventory_report

logger = logging.getLogger(__name__)


def _bool(val) -> bool:
    return bool(val) and val not in ("false", "False", "0", 0)


@dataclass(frozen=True)
class _SyncStep:
    """One ordered operation and the reason it may not apply."""

    name: str
    operation: SyncOperation
    skip_reason: Optional[str] = None


def _execute_step(
    step: _SyncStep,
    results: List[SyncStepResult],
    report: Callable[[str], None],
) -> None:
    """Run one step without allowing its failure to stop later work."""
    if step.skip_reason is not None:
        results.append(
            {
                "name": step.name,
                "status": "skipped",
                "message": step.skip_reason,
            }
        )
        return

    report(f"Sync All: {step.name}")
    try:
        summary = step.operation() or {}
        results.append(
            {
                "name": step.name,
                "status": "ok",
                "summary": cast(JSONObject, summary),
            }
        )
    except Exception as exc:
        logger.warning(
            f"Sync All: {step.name} failed: {exc}",
            exc_info=True,
        )
        results.append(
            {
                "name": step.name,
                "status": "error",
                "message": str(exc),
            }
        )


def _pull_skip_reason(
    master_url: str,
    device_role: DeviceRole,
    is_authority: bool,
) -> Optional[str]:
    if master_url and not is_authority:
        return None
    if is_authority:
        return f"{device_role} role owns its catalog"
    return "master_url not configured"


def _contribution_skip_reason(
    master_url: str,
    device_role: DeviceRole,
    is_authority: bool,
    contribute: bool,
) -> Optional[str]:
    if is_authority:
        return f"{device_role} role owns its catalog"
    if not master_url:
        return "master_url not configured"
    if contribute:
        return None
    return "contribute_to_host is disabled"


def main_run_sync_all(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> Dict:
    """Run all sync operations applicable to this device.

    Returns ``{steps: [{name, status, message, summary?}]}`` where
    ``status`` is one of 'ok', 'skipped', 'error'.
    """
    results: List[SyncStepResult] = []

    master_url = (config.get("master_url") or "").strip()
    device_role = device_role_from_config(config)
    is_authority = is_authority_config(config)
    # Inventory defaults on for authority roles and off for satellites.
    report_inv = config.get("report_inventory_to_host")
    if report_inv is None:
        report_inv = is_authority
    report_inv = _bool(report_inv)

    # contribute_to_host defaults on when this device points at a master.
    contribute = config.get("contribute_to_host")
    if contribute is None:
        contribute = bool(master_url)
    contribute = _bool(contribute)

    def _report(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            event: ProgressEvent = {"message": msg}
            progress_callback(event)

    pull_skip_reason = _pull_skip_reason(
        master_url,
        device_role,
        is_authority,
    )
    contribution_skip_reason = _contribution_skip_reason(
        master_url,
        device_role,
        is_authority,
        contribute,
    )

    steps = [
        _SyncStep(
            "pull_catalog",
            lambda: main_run_catalog_pull(db, config),
            pull_skip_reason,
        ),
        _SyncStep(
            "pull_artist_tags",
            lambda: main_run_artist_tags_pull(db, config),
            pull_skip_reason,
        ),
        _SyncStep(
            "pull_playlists",
            lambda: main_run_playlist_pull(db, config),
            pull_skip_reason,
        ),
        _SyncStep(
            "push_playlists",
            lambda: main_run_playlist_push(db, config),
            pull_skip_reason,
        ),
        # Lyrics ride along last — cheap when there's nothing new
        # (one query on the cursor) and order-independent of other deltas.
        _SyncStep(
            "pull_lyrics",
            lambda: main_run_lyrics_pull(db, config),
            pull_skip_reason,
        ),
        _SyncStep(
            "report_inventory",
            lambda: main_run_inventory_report(db, config),
            None if report_inv else "report_inventory_to_host is disabled",
        ),
        # Contribute local tracks using identifier-first, upload fallback.
        _SyncStep(
            "contribute",
            lambda: main_run_contribute(db, config),
            contribution_skip_reason,
        ),
    ]
    for step in steps:
        _execute_step(step, results, _report)

    summary_line = ", ".join(
        f"{r['name']}={r['status']}" for r in results
    )
    _report(f"Sync All finished: {summary_line}")
    return {"steps": results}
