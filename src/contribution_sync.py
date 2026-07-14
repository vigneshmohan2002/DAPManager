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
from dataclasses import dataclass
from typing import Callable, Optional, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .audio_quality import read_quality
from .config_manager import is_authority_config
from .contracts import (
    ConfigMapping,
    ContributionActionResult,
    ContributionRunResult,
    ContributionStatus,
    ProgressCallback,
)
from .db_manager import DatabaseManager, Track

logger = logging.getLogger(__name__)

CONTRIBUTE_STATE_KEY = "last_contribute"
DEFAULT_BATCH = 50
TERMINAL: set[ContributionStatus] = {"have_better", "satisfied", "ingested"}
VALID_STATUSES: set[ContributionStatus] = TERMINAL | {
    "attempting",
    "needs_upload",
}


@dataclass(frozen=True)
class _ContributionContext:
    """Validated connection details shared by offer and poll operations."""

    master_url: str
    device_id: Optional[str]
    session: requests.Session


@dataclass
class _ContributionCounts:
    """Internal aggregation that emits the unchanged public result shape."""

    offered: int = 0
    uploaded: int = 0
    satisfied: int = 0
    errors: int = 0

    def record_offer(self, status: ContributionStatus) -> None:
        self.offered += 1
        if status == "have_better":
            self.satisfied += 1

    def record_poll(self, status: ContributionStatus) -> None:
        if status == "ingested":
            self.uploaded += 1
        elif status in TERMINAL:
            self.satisfied += 1

    def record_error(self) -> None:
        self.errors += 1

    def merge(self, other: "_ContributionCounts") -> None:
        self.offered += other.offered
        self.uploaded += other.uploaded
        self.satisfied += other.satisfied
        self.errors += other.errors

    def as_result(self) -> ContributionRunResult:
        return {
            "offered": self.offered,
            "uploaded": self.uploaded,
            "satisfied": self.satisfied,
            "errors": self.errors,
        }


def _config_text(config: ConfigMapping, key: str) -> str:
    """Read a string config value while preserving legacy coercion rules."""
    return cast(str, config.get(key) or "").strip()


def _validated_master_status(data: object, operation: str) -> ContributionStatus:
    """Return a canonical contribution status or reject a malformed 2xx body.

    A successful HTTP status alone is not enough to advance local state.  In
    particular, persisting ``NULL`` here makes SQL's ``NOT IN`` semantics skip
    the row forever.  Keep the previous/retryable state when an older or broken
    master omits the contract instead.
    """
    if not isinstance(data, dict):
        raise RuntimeError(f"{operation}: master returned a non-object response")
    if data.get("success") is False:
        raise RuntimeError(
            f"{operation}: master rejected the request: "
            f"{data.get('message') or 'unknown error'}"
        )
    raw_status = data.get("status")
    if not isinstance(raw_status, str):
        raise RuntimeError(f"{operation}: master response omitted status")
    status = raw_status.strip().lower()
    if status not in VALID_STATUSES:
        raise RuntimeError(
            f"{operation}: master returned unknown status {raw_status!r}"
        )
    return cast(ContributionStatus, status)


def _session(api_token: Optional[str] = None) -> requests.Session:
    """Session for both JSON and multipart calls — Content-Type is left to
    each request so file uploads get the right multipart boundary."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    if api_token:
        session.headers["Authorization"] = f"Bearer {api_token}"
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _contribution_context(
    config: ConfigMapping, master_url: str
) -> _ContributionContext:
    """Build the typed remote context after the caller validates the URL."""
    return _ContributionContext(
        master_url=master_url,
        device_id=_config_text(config, "device_id") or None,
        session=_session(api_token=_config_text(config, "api_token") or None),
    )


def _progress_reporter(
    progress_callback: Optional[ProgressCallback],
) -> Callable[[str], None]:
    """Adapt the public structured callback to a message-only reporter."""

    def report(message: str) -> None:
        logger.info(message)
        if progress_callback:
            progress_callback({"message": message})

    return report


def _offer_candidates(
    db: DatabaseManager,
    context: _ContributionContext,
    batch: int,
    report: Callable[[str], None],
) -> _ContributionCounts:
    counts = _ContributionCounts()
    candidates = db.get_contributable_tracks(limit=batch)
    if candidates:
        report(
            f"Offering {len(candidates)} track(s) to {context.master_url}"
        )

    for track in candidates:
        if not track.local_path or not os.path.exists(track.local_path):
            continue
        try:
            status = _offer_track(
                context.session,
                context.master_url,
                context.device_id,
                db,
                track,
            )
            counts.record_offer(status)
        except Exception as exc:
            logger.warning(
                "contribute: offer failed for %s: %s", track.mbid, exc
            )
            counts.record_error()
    return counts


def _poll_pending(
    db: DatabaseManager, context: _ContributionContext
) -> _ContributionCounts:
    counts = _ContributionCounts()
    for row in db.get_pending_contributed():
        contribution_id = row.get("contribution_id")
        mbid = row.get("mbid")
        if not contribution_id:
            continue
        try:
            status = _poll_and_maybe_upload(
                context.session,
                context.master_url,
                db,
                cast(int, contribution_id),
                cast(str, mbid),
            )
            counts.record_poll(status)
        except Exception as exc:
            logger.warning(
                "contribute: poll/upload failed for %s: %s", mbid, exc
            )
            counts.record_error()
    return counts


def main_run_contribute(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
    batch: int = DEFAULT_BATCH,
) -> ContributionRunResult:
    """Offer new local tracks to the master and push uploads for any that the
    master couldn't acquire at equal-or-better quality.

    Returns ``{offered, uploaded, satisfied, errors}``. Raises ValueError when
    a satellite has no ``master_url``.
    """
    if is_authority_config(config):
        # The master is the destination — nothing to contribute upward.
        return {
            "offered": 0,
            "uploaded": 0,
            "satisfied": 0,
            "errors": 0,
            "skipped": "device is master",
        }

    master_url = cast(str, config.get("master_url") or "").rstrip("/")
    if not master_url:
        raise ValueError("master_url is required to contribute from a satellite")

    context = _contribution_context(config, master_url)
    report = _progress_reporter(progress_callback)
    counts = _offer_candidates(db, context, batch, report)
    counts.merge(_poll_pending(db, context))

    _stamp_cursor(db)
    result = counts.as_result()
    report(f"Contribute finished: {result}")
    return result


def main_run_contribute_one(
    db: DatabaseManager, config: ConfigMapping, mbid: str
) -> ContributionActionResult:
    """Offer a single local track to the master and poll once. Used by the
    per-track "Contribute" action. Idempotent: re-running a track already in a
    terminal state is a no-op.

    Returns ``{success, status?, message?, mbid}``.
    """
    if is_authority_config(config):
        return {"success": False, "mbid": mbid, "message": "device is master"}
    master_url = cast(str, config.get("master_url") or "").rstrip("/")
    if not master_url:
        return {
            "success": False,
            "mbid": mbid,
            "message": "master_url not configured",
        }

    track = db.get_track_by_mbid(mbid)
    if track is None or not track.local_path or not os.path.exists(track.local_path):
        return {
            "success": False,
            "mbid": mbid,
            "message": "track has no local file on this device",
        }

    context = _contribution_context(config, master_url)

    existing = db.get_contributed(mbid)
    contribution_id = cast(
        Optional[int], existing.get("contribution_id") if existing else None
    )
    status = cast(
        Optional[ContributionStatus], existing.get("status") if existing else None
    )
    try:
        if contribution_id is None:
            status = _offer_track(
                context.session,
                context.master_url,
                context.device_id,
                db,
                track,
            )
            row = db.get_contributed(mbid)
            contribution_id = cast(
                Optional[int], row.get("contribution_id") if row else None
            )
        if contribution_id and status not in TERMINAL:
            status = _poll_and_maybe_upload(
                context.session,
                context.master_url,
                db,
                contribution_id,
                mbid,
            )
    except Exception as exc:
        logger.warning("contribute_one failed for %s: %s", mbid, exc)
        return {"success": False, "mbid": mbid, "message": str(exc)}

    return {"success": True, "mbid": mbid, "status": status}


def _offer_track(
    session: requests.Session,
    master_url: str,
    device_id: Optional[str],
    db: DatabaseManager,
    track: Track,
) -> ContributionStatus:
    """POST one track offer, persist the returned state, return the status."""
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
    status = _validated_master_status(data, "contribution offer")
    contribution_id = data.get("contribution_id")
    if (
        isinstance(contribution_id, bool)
        or not isinstance(contribution_id, int)
        or contribution_id <= 0
    ):
        raise RuntimeError(
            "contribution offer: master response omitted a valid "
            "contribution_id"
        )
    db.upsert_contributed(track.mbid, contribution_id, status)
    return status


def _poll_and_maybe_upload(
    session: requests.Session,
    master_url: str,
    db: DatabaseManager,
    contribution_id: int,
    mbid: str,
) -> ContributionStatus:
    """Poll one in-flight contribution; upload the file if the master asks.
    Persists and returns the resulting status."""
    resp = session.get(
        f"{master_url}/api/contributions/{contribution_id}", timeout=30
    )
    resp.raise_for_status()
    data = resp.json() or {}
    status = _validated_master_status(data, "contribution poll")
    wants_upload = data.get("want_upload") is True
    if status == "needs_upload" and not wants_upload:
        raise RuntimeError(
            "contribution poll: needs_upload requires want_upload=true"
        )
    if status != "needs_upload" and wants_upload:
        raise RuntimeError(
            "contribution poll: want_upload requires needs_upload status"
        )
    if wants_upload:
        status = _upload_file(
            session, master_url, contribution_id, db, mbid
        )
    db.upsert_contributed(mbid, contribution_id, status)
    return status


def _upload_file(
    session: requests.Session,
    master_url: str,
    contribution_id: int,
    db: DatabaseManager,
    mbid: Optional[str],
) -> ContributionStatus:
    """Stream the local file for ``mbid`` to the master. Returns the resulting
    status string."""
    local_path = db.get_track_local_path(mbid) if mbid else None
    if not local_path or not os.path.exists(local_path):
        logger.warning("contribute: no local file to upload for %s", mbid)
        return "needs_upload"
    with open(local_path, "rb") as fh:
        resp = session.post(
            f"{master_url}/api/contributions/{contribution_id}/upload",
            files={"file": (os.path.basename(local_path), fh)},
            timeout=600,
        )
    resp.raise_for_status()
    data = resp.json() or {}
    return _validated_master_status(data, "contribution upload")


def _stamp_cursor(db: DatabaseManager) -> None:
    cursor = db.conn.cursor()
    try:
        row = cursor.execute("SELECT CURRENT_TIMESTAMP AS ts").fetchone()
        ts = row["ts"] if row is not None else None
    finally:
        cursor.close()
    if ts:
        db.set_sync_state(CONTRIBUTE_STATE_KEY, ts)
