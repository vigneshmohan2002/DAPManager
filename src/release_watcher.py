"""
Watch Lidarr's "wanted / missing" list and route new releases through sldl.

Master-only. The master runs Lidarr as an upgrade sidecar and tracks
artists via Lidarr's MusicBrainz refresh. When Lidarr flips an album into
``wanted/missing``, this watcher enqueues it as a sldl search so Soulseek
gets a shot before Lidarr's own indexer grab (or alongside — the two
paths converge when the file lands in ``music_library_path``).

The watcher is idempotent: ``has_queued_mbid`` blocks a second enqueue on
subsequent ticks. Scheduling is handled by ``SyncScheduler`` — see
``web_server._start_release_watcher``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .db_manager import DatabaseManager, DownloadItem
from .download_request import queue_or_forward
from .lidarr_client import LidarrClient, LidarrError

logger = logging.getLogger(__name__)


def _build_search_query(album: Dict[str, Any]) -> str:
    artist = (album.get("artist") or {}).get("artistName") or ""
    title = album.get("title") or ""
    return f"{artist} - {title}".strip(" -")


def run_watch_tick(
    db: DatabaseManager,
    lidarr: LidarrClient,
    page_size: int = 50,
) -> int:
    """Poll Lidarr for missing albums and queue each new one through sldl.

    Returns the number of albums enqueued this tick (0 when nothing is
    new). Never raises — Lidarr errors log at WARNING and the tick ends
    with 0 so the scheduler keeps running.
    """
    try:
        missing = lidarr.get_wanted_missing(page_size=page_size)
    except LidarrError as e:
        logger.warning("release_watcher: Lidarr fetch failed: %s", e)
        return 0

    queued = 0
    for album in missing:
        mbid = album.get("foreignAlbumId") or ""
        if not mbid:
            logger.debug(
                "release_watcher: skipping album without foreignAlbumId: %s",
                album.get("title"),
            )
            continue
        if db.has_queued_mbid(mbid):
            continue

        query = _build_search_query(album)
        if not query:
            logger.debug(
                "release_watcher: skipping album without artist/title (mbid=%s)",
                mbid,
            )
            continue

        item = DownloadItem(
            search_query=query,
            playlist_id="",
            mbid_guess=mbid,
        )
        row_id = queue_or_forward(db, item)
        queued += 1
        logger.info(
            "release_watcher: enqueued '%s' (mbid=%s, row=%s)",
            query, mbid, row_id,
        )

    if queued:
        logger.info("release_watcher: %d new album(s) queued this tick.", queued)
    else:
        logger.debug("release_watcher: tick found no new releases.")
    return queued
