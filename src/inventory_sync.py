"""
Opt-in inventory reporting.

A satellite with ``report_inventory_to_host: true`` collects its MBID →
local_path map and POSTs it to the master's /api/inventory. A master with
the same flag records the snapshot against its own device_id locally so
it shows up in the fleet view alongside satellites.

Reports are idempotent: the master replaces the whole device snapshot on
each call, so retrying after a network blip is always safe.
"""

import logging
from typing import Any, Callable, List, Literal, Mapping, Optional, TypedDict, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config_manager import is_authority_config
from .contracts import ConfigMapping, ProgressCallback
from .db_manager import DatabaseManager

logger = logging.getLogger(__name__)

INVENTORY_REPORT_STATE_KEY = "last_inventory_report"


class InventoryItem(TypedDict):
    """One device-local track advertised to the master."""

    mbid: str
    local_path: str


class InventoryReportResult(TypedDict):
    """Stable summary returned by :func:`main_run_inventory_report`."""

    mode: Literal["local", "remote"]
    device_id: str
    items: int
    written: int


def _config_text(config: ConfigMapping, key: str) -> str:
    """Read a string config value while preserving legacy coercion rules."""
    return cast(str, config.get(key) or "").strip()


def _build_items(db: DatabaseManager) -> List[InventoryItem]:
    return [
        {"mbid": mbid, "local_path": path}
        for mbid, path in db.get_mbid_to_track_path_map().items()
    ]


def _session(api_token: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"Accept": "application/json", "Content-Type": "application/json"}
    )
    if api_token:
        session.headers["Authorization"] = f"Bearer {api_token}"
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["POST"]),
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _progress_reporter(
    progress_callback: Optional[ProgressCallback],
) -> Callable[[str], None]:
    """Adapt the public structured callback to a message-only reporter."""

    def report(message: str) -> None:
        logger.info(message)
        if progress_callback:
            progress_callback({"message": message})

    return report


def _record_local_inventory(
    db: DatabaseManager,
    device_id: str,
    items: List[InventoryItem],
    report: Callable[[str], None],
) -> InventoryReportResult:
    report(f"Recording own inventory ({len(items)} items)")
    written = db.replace_device_inventory(device_id, items)
    _stamp_cursor(db)
    return {
        "mode": "local",
        "device_id": device_id,
        "items": len(items),
        "written": written,
    }


def _record_remote_inventory(
    db: DatabaseManager,
    config: ConfigMapping,
    device_id: str,
    master_url: str,
    items: List[InventoryItem],
    report: Callable[[str], None],
) -> InventoryReportResult:
    report(f"Reporting inventory to {master_url} ({len(items)} items)")
    session = _session(api_token=_config_text(config, "api_token") or None)
    response = session.post(
        f"{master_url}/api/inventory",
        json={"device_id": device_id, "items": items},
        timeout=60,
    )
    response.raise_for_status()
    data = cast(Mapping[str, Any], response.json() or {})
    if data.get("success"):
        _stamp_cursor(db)
        return {
            "mode": "remote",
            "device_id": device_id,
            "items": len(items),
            "written": int(data.get("written", 0)),
        }

    raise RuntimeError(
        f"Master rejected inventory: {data.get('message', 'unknown error')}"
    )


def main_run_inventory_report(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> InventoryReportResult:
    """Collect and publish this device's inventory.

    Returns {mode, device_id, items, written}. ``mode`` is 'local' when
    writing to this device's own DB (master role) or 'remote' when
    POSTing to the master. Raises ValueError if the device lacks a
    ``device_id`` or if a satellite has no ``master_url`` configured.
    """
    device_id = _config_text(config, "device_id")
    if not device_id:
        raise ValueError("device_id is missing from config")

    items = _build_items(db)
    report = _progress_reporter(progress_callback)

    if is_authority_config(config):
        return _record_local_inventory(db, device_id, items, report)

    master_url = cast(str, config.get("master_url") or "").rstrip("/")
    if not master_url:
        raise ValueError("master_url is required to report inventory from a satellite")

    return _record_remote_inventory(
        db, config, device_id, master_url, items, report
    )


def _stamp_cursor(db: DatabaseManager) -> None:
    """Persist the successful-report timestamp for the sync status widget."""
    cursor = db.conn.cursor()
    try:
        row = cursor.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()
        ts = row["ts"] if row is not None else None
    finally:
        cursor.close()
    if ts:
        db.set_sync_state(INVENTORY_REPORT_STATE_KEY, ts)
