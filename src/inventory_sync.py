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
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .db_manager import DatabaseManager

logger = logging.getLogger(__name__)

INVENTORY_REPORT_STATE_KEY = "last_inventory_report"


def _build_items(db: DatabaseManager) -> list:
    return [
        {"mbid": mbid, "local_path": path}
        for mbid, path in db.get_mbid_to_track_path_map().items()
    ]


def _session(api_token: Optional[str] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    if api_token:
        s.headers["Authorization"] = f"Bearer {api_token}"
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["POST"]),
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


def main_run_inventory_report(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Collect and publish this device's inventory.

    Returns {mode, device_id, items, written}. ``mode`` is 'local' when
    writing to this device's own DB (master role) or 'remote' when
    POSTing to the master. Raises ValueError if the device lacks a
    ``device_id`` or if a satellite has no ``master_url`` configured.
    """
    device_id = (config.get("device_id") or "").strip()
    if not device_id:
        raise ValueError("device_id is missing from config")

    items = _build_items(db)
    role = (config.get("device_role") or "satellite").strip()

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback({"message": msg})

    if role == "master":
        _report(f"Recording own inventory ({len(items)} items)")
        written = db.replace_device_inventory(device_id, items)
        _stamp_cursor(db)
        return {
            "mode": "local",
            "device_id": device_id,
            "items": len(items),
            "written": written,
        }

    master_url = (config.get("master_url") or "").rstrip("/")
    if not master_url:
        raise ValueError("master_url is required to report inventory from a satellite")

    _report(f"Reporting inventory to {master_url} ({len(items)} items)")
    api_token = (config.get("api_token") or "").strip() or None
    session = _session(api_token=api_token)
    resp = session.post(
        f"{master_url}/api/inventory",
        json={"device_id": device_id, "items": items},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if not data.get("success"):
        raise RuntimeError(
            f"Master rejected inventory: {data.get('message', 'unknown error')}"
        )

    _stamp_cursor(db)
    return {
        "mode": "remote",
        "device_id": device_id,
        "items": len(items),
        "written": int(data.get("written", 0)),
    }


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
