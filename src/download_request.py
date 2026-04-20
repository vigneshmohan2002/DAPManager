"""
Route download requests to the master or queue locally.

On a master (or when no master_url is configured), downloads go into the
local queue. On a satellite with a master_url set, we POST to the master
so its sldl + Lidarr stack does the actual fetching; the imported file
flows back via catalog sync. Call sites should use queue_or_forward
instead of db.queue_download so the routing happens in one place.
"""

import logging

import requests

from .config_manager import get_config
from .db_manager import DatabaseManager, DownloadItem

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SEC = 8


def queue_or_forward(db: DatabaseManager, item: DownloadItem) -> int:
    """Queue on the master when configured, else on the local DB.

    Returns the master's reported item id (0 when the master says it was
    already queued), or the local row id. Any network / protocol error
    falls back to a local queue so the user isn't silently stranded.
    """
    config = get_config()
    if config.is_master or not config.master_url:
        return db.queue_download(item)

    url = f"{config.master_url}/api/download/request"
    payload = {
        "search_query": item.search_query,
        "mbid_guess": item.mbid_guess or "",
        "playlist_id": item.playlist_id or "",
    }
    headers = {}
    token = (config.get("api_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT_SEC)
    except requests.RequestException as e:
        logger.warning("Master unreachable (%s); queuing locally: %s", url, e)
        return db.queue_download(item)

    if resp.status_code != 200:
        logger.warning(
            "Master rejected download request (%s): %s — queuing locally",
            resp.status_code, resp.text[:200],
        )
        return db.queue_download(item)

    try:
        data = resp.json() or {}
    except ValueError:
        logger.warning("Master returned non-JSON; queuing locally")
        return db.queue_download(item)

    if not data.get("success"):
        logger.warning(
            "Master returned success=false (%s); queuing locally",
            data.get("message"),
        )
        return db.queue_download(item)

    return int(data.get("item_id") or 0)
