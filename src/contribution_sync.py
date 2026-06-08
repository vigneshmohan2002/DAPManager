"""
Satellite → master track contribution.

A satellite offers tracks it has locally to the master. For each new track it
POSTs an identifier + quality descriptor; the master first tries to acquire the
track itself (cheap). Only when the master can't match the satellite's quality
does the satellite upload the actual bytes.

State lives in the ``contributed`` table so we don't re-offer every sync and so
in-flight offers get polled until they reach a terminal state
(have_better | satisfied | ingested).
"""

import logging
import os
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .db_manager import DatabaseManager
from .audio_quality import read_quality

logger = logging.getLogger(__name__)

CONTRIBUTE_STATE_KEY = "last_contribute"
DEFAULT_BATCH = 50
TERMINAL = {"have_better", "satisfied", "ingested"}


def _session(api_token: Optional[str] = None) -> requests.Session:
    """Session for both JSON and multipart calls — Content-Type is left to
    each request so file uploads get the right multipart boundary."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    if api_token:
        s.headers["Authorization"] = f"Bearer {api_token}"
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


def main_run_contribute(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
    batch: int = DEFAULT_BATCH,
) -> dict:
    """Offer new local tracks to the master and push uploads for any that the
    master couldn't acquire at equal-or-better quality.

    Returns ``{offered, uploaded, satisfied, errors}``. Raises ValueError when
    a satellite has no ``master_url``.
    """
    role = (config.get("device_role") or "satellite").strip()
    if role == "master":
        # The master is the destination — nothing to contribute upward.
        return {"offered": 0, "uploaded": 0, "satisfied": 0, "errors": 0,
                "skipped": "device is master"}

    master_url = (config.get("master_url") or "").rstrip("/")
    if not master_url:
        raise ValueError("master_url is required to contribute from a satellite")

    device_id = (config.get("device_id") or "").strip() or None
    api_token = (config.get("api_token") or "").strip() or None
    session = _session(api_token=api_token)

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback({"message": msg})

    offered = uploaded = satisfied = errors = 0

    # 1) Offer tracks we've never contributed before.
    candidates = db.get_contributable_tracks(limit=batch)
    if candidates:
        _report(f"Offering {len(candidates)} track(s) to {master_url}")
    for track in candidates:
        if not track.local_path or not os.path.exists(track.local_path):
            continue
        try:
            quality = read_quality(track.local_path)
            resp = session.post(
                f"{master_url}/api/contributions",
                json={
                    "device_id": device_id,
                    "mbid": track.mbid,
                    "isrc": getattr(track, "isrc", None),
                    "artist": track.artist,
                    "title": track.title,
                    "album": track.album,
                    "quality": quality,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            db.upsert_contributed(
                track.mbid, data.get("contribution_id"), data.get("status")
            )
            offered += 1
            if data.get("status") == "have_better":
                satisfied += 1
        except Exception as e:
            logger.warning("contribute: offer failed for %s: %s", track.mbid, e)
            errors += 1

    # 2) Poll in-flight offers; upload the bytes when the master asks.
    for row in db.get_pending_contributed():
        cid = row.get("contribution_id")
        mbid = row.get("mbid")
        if not cid:
            continue
        try:
            resp = session.get(
                f"{master_url}/api/contributions/{cid}", timeout=30
            )
            resp.raise_for_status()
            data = resp.json() or {}
            status = data.get("status")
            if data.get("want_upload"):
                status = _upload_file(session, master_url, cid, db, mbid)
                if status == "ingested":
                    uploaded += 1
            elif status in TERMINAL:
                satisfied += 1 if status != "ingested" else 0
            db.upsert_contributed(mbid, cid, status)
        except Exception as e:
            logger.warning("contribute: poll/upload failed for %s: %s", mbid, e)
            errors += 1

    _stamp_cursor(db)
    result = {
        "offered": offered, "uploaded": uploaded,
        "satisfied": satisfied, "errors": errors,
    }
    _report(f"Contribute finished: {result}")
    return result


def _upload_file(session, master_url, cid, db, mbid) -> Optional[str]:
    """Stream the local file for ``mbid`` to the master. Returns the resulting
    status string."""
    local_path = db.get_track_local_path(mbid) if mbid else None
    if not local_path or not os.path.exists(local_path):
        logger.warning("contribute: no local file to upload for %s", mbid)
        return "needs_upload"
    with open(local_path, "rb") as fh:
        resp = session.post(
            f"{master_url}/api/contributions/{cid}/upload",
            files={"file": (os.path.basename(local_path), fh)},
            timeout=600,
        )
    resp.raise_for_status()
    data = resp.json() or {}
    return data.get("status", "needs_upload")


def _stamp_cursor(db: DatabaseManager) -> None:
    cursor = db.conn.cursor()
    try:
        row = cursor.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()
        ts = row["ts"] if row is not None else None
    finally:
        cursor.close()
    if ts:
        db.set_sync_state(CONTRIBUTE_STATE_KEY, ts)
